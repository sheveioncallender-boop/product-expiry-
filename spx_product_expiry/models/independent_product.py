from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ProductTemplate(models.Model):
    _inherit = "product.template"

    spx_expiry_enabled = fields.Boolean(string="Independent Expiry", copy=False,
        help="Assign existing stock to expiry dates without enabling native lot tracking.")
    spx_current_company_id = fields.Many2one("res.company", compute="_compute_independent_expiry")
    spx_independent_batch_ids = fields.One2many("spx.expiry.batch", "product_tmpl_id", string="Expiry Stock", copy=False)
    spx_expiry_assigned = fields.Float(string="Total Assigned to Expiries", compute="_compute_independent_expiry")
    spx_expiry_unassigned = fields.Float(string="Needs Expiry Allocation", compute="_compute_independent_expiry",
        help="Odoo stock less pending POS sales and expiry quantities. A negative number means expiry quantities need correction.")
    spx_expiry_physical = fields.Float(string="Odoo Stock", compute="_compute_independent_expiry")
    spx_independent_next_expiry = fields.Date(string="Next Available Expiry", compute="_compute_independent_expiry")

    @api.depends("spx_independent_batch_ids.quantity", "spx_independent_batch_ids.expiry_date", "spx_independent_batch_ids.saleable")
    @api.depends_context("company")
    def _compute_independent_expiry(self):
        Batch = self.env["spx.expiry.batch"]
        for template in self:
            template.spx_current_company_id = self.env.company
            summaries = [Batch._summary(p, self.env.company) for p in template.product_variant_ids] if template.spx_expiry_enabled else []
            template.spx_expiry_assigned = sum(s["assigned"] for s in summaries)
            template.spx_expiry_unassigned = sum(s["unassigned"] for s in summaries)
            template.spx_expiry_physical = sum(s["physical"] for s in summaries)
            dates = template.spx_independent_batch_ids.filtered(lambda b: b.company_id == self.env.company and b.quantity > 0 and b.saleable).mapped("expiry_date")
            template.spx_independent_next_expiry = min(dates, default=False)

    @api.constrains("spx_expiry_enabled", "tracking", "is_storable")
    def _check_independent_mode(self):
        for product in self:
            if product.spx_expiry_enabled and (product.tracking != "none" or not product.is_storable):
                raise ValidationError(self.env._("Independent Expiry requires Track Inventory: By Quantity."))
            if not product.spx_expiry_enabled and self.env["spx.expiry.batch"].sudo().search_count([
                ("product_tmpl_id", "=", product.id), ("quantity", ">", 0)
            ]):
                raise ValidationError(self.env._("Clear or reconcile remaining expiry quantities before disabling Independent Expiry."))

    @api.model
    def _load_pos_data_fields(self, config):
        return super()._load_pos_data_fields(config) + ["spx_expiry_enabled"]

    def action_spx_independent_batches(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("spx_product_expiry.action_independent_expiry")
        action.update(domain=[("product_tmpl_id", "=", self.id)], context={
            "default_product_id": self.product_variant_id.id if self.product_variant_count == 1 else False,
            "default_company_id": self.env.company.id,
        })
        return action
