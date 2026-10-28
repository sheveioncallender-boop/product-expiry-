from collections import defaultdict
import math

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.float_utils import float_compare


class PosSession(models.Model):
    _inherit = "pos.session"

    @api.model
    def _load_pos_data_models(self, config):
        return super()._load_pos_data_models(config) + ["spx.expiry.batch"]


class PosOrderLine(models.Model):
    _inherit = "pos.order.line"

    spx_expiry_batch_id = fields.Many2one("spx.expiry.batch", string="Expiry Batch", ondelete="restrict", check_company=True)
    spx_expiry_label = fields.Char(string="Expiry Selected", copy=False)
    spx_return_saleable = fields.Boolean(string="Return to Saleable Expiry Stock", default=False, copy=False)

    @api.model
    def _load_pos_data_fields(self, config):
        return super()._load_pos_data_fields(config) + ["spx_expiry_batch_id", "spx_expiry_label", "spx_return_saleable"]

    def write(self, vals):
        if {"qty", "product_id", "spx_expiry_batch_id", "spx_return_saleable", "refunded_orderline_id", "order_id"} & vals.keys():
            if self.env["spx.expiry.movement"].sudo().search_count([("pos_line_id", "in", self.ids)]):
                raise UserError(self.env._("A completed expiry sale cannot be edited. Use a linked refund."))
        return super().write(vals)


class PosOrder(models.Model):
    _inherit = "pos.order"

    def _spx_stock_completed(self):
        self.ensure_one()
        if self.picking_ids:
            return all(p.state == "done" for p in self.picking_ids)
        return False

    def _should_create_picking_real_time(self):
        # Keep expiry sales and native stock in the same transaction even when
        # ordinary POS products are configured to update stock at session close.
        return any(self.lines.product_id.mapped("spx_expiry_enabled")) or super()._should_create_picking_real_time()

    def _spx_apply_expiry(self):
        self.ensure_one()
        Batch = self.env["spx.expiry.batch"]
        Movement = self.env["spx.expiry.movement"].sudo()
        lines = self.lines.filtered(lambda l: l.product_id.spx_expiry_enabled and l.qty)
        if not lines:
            return
        if self.shipping_date:
            raise UserError(self.env._("Independent expiry products must be handed over at the till. Ship-later orders are not supported."))
        Batch._lock_products(lines.product_id)
        tz = self.company_id.partner_id.tz or "America/Port_of_Spain"
        today = fields.Date.context_today(self.with_context(tz=tz))
        # Check consistency before any deduction, and validate refunds against the
        # original persisted sale, never a batch or quantity supplied by the UI.
        for product in lines.product_id:
            summary = Batch._summary(product, self.company_id)
            if float_compare(summary["unassigned"], 0, precision_rounding=product.uom_id.rounding) < 0:
                raise UserError(self.env._("Expiry quantities for %s need reconciliation with Odoo stock before selling.", product.display_name))
        for line in lines.sorted("id"):
            if Movement.search_count([("pos_line_id", "=", line.id)]):
                continue
            if not math.isfinite(line.qty):
                raise UserError(self.env._("Invalid expiry quantity."))
            batch = line.spx_expiry_batch_id
            if line.qty < 0:
                original = line.refunded_orderline_id
                original_movement = Movement.search([("pos_line_id", "=", original.id)], limit=1) if original else Movement
                if not original or original.product_id != line.product_id or original.order_id.company_id != self.company_id or not original_movement or original_movement.delta >= 0:
                    raise UserError(self.env._("Refund expiry products from their original POS receipt."))
                returned = sum(Movement.search([("pos_line_id.refunded_orderline_id", "=", original.id)]).mapped("delta"))
                if float_compare(returned - line.qty, -original_movement.delta, precision_rounding=line.product_id.uom_id.rounding) > 0:
                    raise UserError(self.env._("The refund exceeds the original expiry batch quantity sold."))
                batch = original.spx_expiry_batch_id
                line.spx_expiry_batch_id = batch
                if not line.spx_return_saleable:
                    held = Batch.sudo().search([("product_id", "=", line.product_id.id), ("company_id", "=", self.company_id.id),
                                                ("expiry_date", "=", batch.expiry_date), ("saleable", "=", False),
                                                ("name", "=", "Returned: " + batch.name)], limit=1)
                    batch = held or Batch.sudo().create({"product_id": line.product_id.id, "company_id": self.company_id.id,
                        "expiry_date": batch.expiry_date, "name": "Returned: " + batch.name, "saleable": False})
            if not batch or batch.company_id != self.company_id or batch.product_id != line.product_id:
                raise UserError(self.env._("Select the correct expiry batch for %s.", line.product_id.display_name))
            if line.qty > 0 and (not batch.active or not batch.saleable or batch.expiry_date < today):
                raise UserError(self.env._("This expiry batch is expired, inactive or on hold. Select saleable stock."))
            line.spx_expiry_label = "%s · %s" % (batch.name, batch.expiry_date)
            batch._change_quantity(-line.qty, "POS %s" % self.pos_reference, pos_line=line)

    def _process_saved_order(self, draft):
        if not draft and self.state != "cancel":
            self._spx_apply_expiry()
        result = super()._process_saved_order(draft)
        if not draft and self.state != "cancel" and any(self.lines.product_id.mapped("spx_expiry_enabled")) and not self._spx_stock_completed():
            raise UserError(self.env._("Odoo could not complete the stock transfer for this expiry sale or refund. Resolve the stock transfer issue before validating; no expiry quantities have been committed."))
        return result


class PosConfig(models.Model):
    _inherit = "pos.config"

    def spx_check_expiry(self, lines):
        """Online preflight only. Final acceptance is atomic in _process_saved_order."""
        self.ensure_one()
        self.check_access("read")
        if not self.env.user.has_group("point_of_sale.group_pos_user") or self.company_id not in self.env.companies:
            raise AccessError(self.env._("Point of Sale access is required."))
        totals = defaultdict(float)
        Batch = self.env["spx.expiry.batch"]
        today = fields.Date.context_today(self.with_context(tz=self.company_id.partner_id.tz or "America/Port_of_Spain"))
        for data in lines:
            product = self.env["product.product"].browse(data["product_id"]).exists()
            product.check_access("read")
            if not product.spx_expiry_enabled:
                continue
            qty = float(data["qty"])
            if not math.isfinite(qty):
                raise UserError(self.env._("Invalid quantity."))
            if qty < 0:
                if not data.get("refunded_orderline_id"):
                    raise UserError(self.env._("Use the original receipt to refund expiry products."))
                continue
            batch = Batch.browse(data.get("batch_id")).exists()
            batch.check_access("read")
            if not batch or batch.product_id != product or batch.company_id != self.company_id or not batch.active or not batch.saleable or batch.expiry_date < today:
                raise UserError(self.env._("Select a valid expiry batch for %s.", product.display_name))
            totals[batch.id] += qty
        for batch in Batch.browse(list(totals)):
            if float_compare(totals[batch.id], batch.quantity, precision_rounding=batch.uom_id.rounding) > 0:
                raise UserError(self.env._("Only %(qty)s remain in %(batch)s. Reduce the quantity or split the sale across expiry batches.", qty=batch.quantity, batch=batch.name))
            batch._check_allocation()
        return True
