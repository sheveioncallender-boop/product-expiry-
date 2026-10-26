from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    spx_expiry_lot_ids = fields.One2many(
        "stock.lot", "spx_product_tmpl_id", string="Expiry Batches",
        groups="stock.group_stock_user", copy=False,
    )
    spx_next_expiration_date = fields.Datetime(
        string="Next Expiry", compute="_compute_spx_expiry_summary",
        groups="stock.group_stock_user",
        help="Earliest expiry among batches with positive native on-hand quantity in the current stock context. Includes expired stock.",
    )
    spx_stocked_lot_count = fields.Integer(
        string="Batches in Stock", compute="_compute_spx_expiry_summary", groups="stock.group_stock_user",
    )
    spx_undated_lot_count = fields.Integer(
        string="Batches Missing Expiry", compute="_compute_spx_expiry_summary", groups="stock.group_stock_user",
    )

    @api.depends("spx_expiry_lot_ids.expiration_date", "spx_expiry_lot_ids.product_qty")
    @api.depends_context("allowed_company_ids", "location", "warehouse_id", "owner_id", "package_id", "to_date", "owners")
    def _compute_spx_expiry_summary(self):
        # No sudo: native lot/quant visibility and stock quantity computations apply.
        for product in self:
            stocked = product.spx_expiry_lot_ids.filtered(lambda lot: lot.product_qty > 0)
            dated = stocked.filtered("expiration_date")
            product.spx_stocked_lot_count = len(stocked)
            product.spx_undated_lot_count = len(stocked - dated)
            product.spx_next_expiration_date = min(dated.mapped("expiration_date"), default=False)

    def action_spx_expiry_batches(self):
        self.ensure_one()
        self.env["stock.lot"]._spx_check_expiry_access()
        self.check_access("read")
        action = self.env["ir.actions.actions"]._for_xml_id("spx_product_expiry.action_expiry_batches")
        action["domain"] = [("spx_product_tmpl_id", "=", self.id)]
        action["context"] = dict(self.env.context, search_default_in_stock=1)
        return action
