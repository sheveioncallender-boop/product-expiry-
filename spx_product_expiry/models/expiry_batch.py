from datetime import timedelta
import math

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_compare


class ExpiryBatch(models.Model):
    _name = "spx.expiry.batch"
    _description = "Independent Product Expiry"
    _inherit = ["mail.thread", "pos.load.mixin"]
    _order = "expiry_date, id"
    _check_company_auto = True

    name = fields.Char(string="Batch / Reference", required=True, default="Expiry stock", tracking=True)
    product_id = fields.Many2one("product.product", required=True, index=True, ondelete="restrict", check_company=True)
    product_tmpl_id = fields.Many2one(related="product_id.product_tmpl_id", store=True, index=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda s: s.env.company, index=True)
    expiry_date = fields.Date(required=True, index=True, tracking=True)
    quantity = fields.Float(string="Remaining Quantity", digits="Product Unit", default=0, tracking=True)
    uom_id = fields.Many2one(related="product_id.uom_id")
    saleable = fields.Boolean(string="Available for Sale", default=True, tracking=True)
    active = fields.Boolean(default=True)
    days_remaining = fields.Integer(compute="_compute_status")
    status = fields.Selection([("expired", "Expired"), ("today", "Expires Today"),
                              ("soon", "Within 30 Days"), ("later", "More Than 30 Days"),
                              ("hold", "On Hold")], compute="_compute_status", search="_search_status")
    movement_ids = fields.One2many("spx.expiry.movement", "batch_id", string="Quantity History")

    @api.depends("expiry_date", "saleable")
    @api.depends_context("tz")
    def _compute_status(self):
        today = fields.Date.context_today(self)
        for batch in self:
            days = (batch.expiry_date - today).days if batch.expiry_date else 0
            batch.days_remaining = days
            batch.status = "hold" if not batch.saleable else "expired" if days < 0 else "today" if days == 0 else "soon" if days <= 30 else "later"

    @api.model
    def _search_status(self, operator, value):
        from odoo.fields import Domain
        if operator not in ("=", "!=", "in", "not in"):
            return NotImplemented
        today = fields.Date.context_today(self)
        domains = {
            "hold": Domain("saleable", "=", False),
            "expired": Domain("expiry_date", "<", today) & Domain("saleable", "=", True),
            "today": Domain("expiry_date", "=", today) & Domain("saleable", "=", True),
            "soon": Domain("expiry_date", ">", today) & Domain("expiry_date", "<=", today + timedelta(days=30)) & Domain("saleable", "=", True),
            "later": Domain("expiry_date", ">", today + timedelta(days=30)) & Domain("saleable", "=", True),
        }
        values = [value] if operator in ("=", "!=") else value
        result = Domain.OR([domains.get(v, Domain.FALSE) for v in values])
        return ~result if operator in ("!=", "not in") else result

    @api.model
    def _lock_products(self, products):
        # The product lock also serializes first-time allocations (no batch row yet).
        products.flush_recordset()
        if products:
            self.env.cr.execute("SELECT id FROM product_product WHERE id IN %s ORDER BY id FOR UPDATE", [tuple(products.ids)])
            # Odoo uses REPEATABLE READ: a lock alone cannot refresh a transaction's
            # old snapshot. Touch the row version so a waiting competing transaction
            # gets a serialization failure and Odoo retries it with fresh balances.
            self.env.cr.execute("UPDATE product_product SET write_date = write_date WHERE id IN %s", [tuple(products.ids)])
            self.invalidate_model(["quantity"])
            self.env["stock.quant"].invalidate_model()

    @api.model
    def _physical_quantity(self, product, company):
        return sum(self.env["stock.quant"].sudo().search([
            ("product_id", "=", product.id), ("company_id", "=", company.id),
            ("location_id.usage", "=", "internal"), ("owner_id", "=", False),
        ]).mapped("quantity"))

    @api.model
    def _pending_pos_quantity(self, product, company):
        # POS configurations may postpone native pickings until session closing.
        # Deduct accepted expiry sales until their native moves have been completed.
        movements = self.env["spx.expiry.movement"].sudo().search([
            ("batch_id.product_id", "=", product.id), ("company_id", "=", company.id),
            ("pos_line_id", "!=", False),
            "|", ("pos_line_id.order_id.picking_ids", "=", False), ("pos_line_id.order_id.picking_ids.state", "!=", "done"),
        ])
        return -sum(m.delta for m in movements if not m.pos_line_id.order_id._spx_stock_completed())

    @api.model
    def _summary(self, product, company):
        batches = self.sudo().with_context(active_test=False).search([
            ("product_id", "=", product.id), ("company_id", "=", company.id),
        ])
        assigned = sum(batches.mapped("quantity"))
        physical = self._physical_quantity(product, company)
        pending = self._pending_pos_quantity(product, company)
        return {"assigned": assigned, "physical": physical, "pending": pending,
                "unassigned": physical - pending - assigned}

    def _check_allocation(self):
        for product, company in {(b.product_id, b.company_id) for b in self}:
            summary = self._summary(product, company)
            if float_compare(summary["unassigned"], 0, precision_rounding=product.uom_id.rounding) < 0:
                raise ValidationError(self.env._("Expiry quantities for %(product)s exceed its available Odoo stock. Assigned: %(assigned)s; stock after pending POS sales: %(stock)s. Correct the allocation before continuing.", product=product.display_name, assigned=summary["assigned"], stock=summary["physical"] - summary["pending"]))

    @api.constrains("quantity", "product_id", "company_id", "expiry_date", "active")
    def _check_batch(self):
        for batch in self:
            if not math.isfinite(batch.quantity) or batch.quantity < 0:
                raise ValidationError(self.env._("Remaining quantity must be a finite, non-negative number."))
            if not batch.active and batch.quantity:
                raise ValidationError(self.env._("A batch with remaining stock cannot be archived."))
            if batch.product_id.tracking != "none" or not batch.product_id.is_storable:
                raise ValidationError(self.env._("Independent expiry is for stockable products tracked By Quantity. Native lot/serial products keep their existing tracking."))
            if not batch.product_id.spx_expiry_enabled:
                raise ValidationError(self.env._("Enable Independent Expiry on the product first."))

    @api.model_create_multi
    def create(self, vals_list):
        products = self.env["product.product"].browse([v["product_id"] for v in vals_list if v.get("product_id")])
        self._lock_products(products)
        batches = super().create(vals_list)
        batches._check_allocation()
        for batch in batches:
            batch._log_change(batch.quantity, "Opening expiry allocation")
        return batches

    def write(self, vals):
        if {"product_id", "company_id"} & vals.keys():
            raise UserError(self.env._("Create a new expiry entry instead of moving an existing entry to another product or company."))
        self._lock_products(self.product_id)
        before = {b.id: b.quantity for b in self}
        result = super().write(vals)
        if "quantity" in vals:
            self.filtered(lambda b: b.quantity > before[b.id])._check_allocation()
            for batch in self:
                batch._log_change(batch.quantity - before[batch.id], "Manual expiry allocation correction")
        return result

    def unlink(self):
        if any(b.quantity or b.movement_ids for b in self):
            raise UserError(self.env._("Expiry entries with stock or history must be kept. Set an empty entry inactive instead."))
        return super().unlink()

    def _log_change(self, delta, reason, pos_line=None, move_line=None):
        self.ensure_one()
        if not delta:
            return
        # No public write API is granted on history or cashier balances. All callers
        # are private validated transaction paths, and the real actor is retained.
        self.env["spx.expiry.movement"].sudo().create({
            "batch_id": self.id, "delta": delta, "balance": self.quantity,
            "reason": reason, "user_id": self.env.uid,
            "pos_line_id": pos_line.id if pos_line else False,
            "move_line_id": move_line.id if move_line else False,
        })

    def _change_quantity(self, delta, reason, pos_line=None, move_line=None):
        self.ensure_one()
        self._lock_products(self.product_id)
        new_quantity = self.quantity + delta
        if not math.isfinite(new_quantity) or float_compare(new_quantity, 0, precision_rounding=self.uom_id.rounding) < 0:
            raise UserError(self.env._("Not enough expiry stock for %(product)s / %(batch)s. Remaining: %(qty)s.", product=self.product_id.display_name, batch=self.name, qty=self.quantity))
        super(ExpiryBatch, self.sudo()).write({"quantity": max(new_quantity, 0)})
        self._log_change(delta, reason, pos_line, move_line)

    @api.model
    def pos_choices(self, product_id, config_id):
        if not self.env.user.has_group("point_of_sale.group_pos_user"):
            raise AccessError(self.env._("Point of Sale access is required."))
        config = self.env["pos.config"].browse(config_id).exists()
        config.check_access("read")
        product = self.env["product.product"].browse(product_id).exists()
        product.check_access("read")
        if not config or config.company_id not in self.env.companies or not product:
            raise AccessError(self.env._("Invalid product or POS company."))
        today = fields.Date.context_today(self.with_context(tz=config.company_id.partner_id.tz or "America/Port_of_Spain"))
        batches = self.search([("product_id", "=", product.id), ("company_id", "=", config.company_id.id),
                               ("saleable", "=", True), ("expiry_date", ">=", today), ("quantity", ">", 0)])
        summary = self._summary(product, config.company_id)
        return {"batches": batches.read(["name", "expiry_date", "quantity"], load=False),
                "unassigned": summary["unassigned"]}

    @api.model
    def _load_pos_data_domain(self, data, config):
        # Include empty/held batches for historical orders and refunds.
        return [("company_id", "=", config.company_id.id)]

    @api.model
    def _load_pos_data_fields(self, config):
        return ["id", "name", "product_id", "company_id", "expiry_date", "quantity", "saleable", "active", "write_date"]


class ExpiryMovement(models.Model):
    _name = "spx.expiry.movement"
    _description = "Expiry Quantity History"
    _order = "id desc"

    batch_id = fields.Many2one("spx.expiry.batch", required=True, ondelete="restrict", index=True)
    company_id = fields.Many2one(related="batch_id.company_id", store=True, index=True)
    delta = fields.Float(digits="Product Unit")
    balance = fields.Float(digits="Product Unit")
    reason = fields.Char(required=True)
    user_id = fields.Many2one("res.users", required=True)
    pos_line_id = fields.Many2one("pos.order.line", ondelete="restrict", index=True)
    move_line_id = fields.Many2one("stock.move.line", ondelete="restrict", index=True)
    _pos_once = models.Constraint("UNIQUE(pos_line_id)", "This POS line has already updated expiry stock.")
    _move_once = models.Constraint("UNIQUE(move_line_id)", "This stock line has already updated expiry stock.")
