from odoo import api, fields, models


class ExpiryReport(models.AbstractModel):
    _name = "report.spx_product_expiry.expiry_report_document"
    _description = "Product Expiry Report PDF"

    @api.model
    def _get_report_values(self, docids, data=None):
        self.env["stock.lot"]._spx_check_expiry_access()
        wizard = self.env["spx.expiry.report.wizard"].browse(docids).exists()
        wizard.ensure_one()
        lots = wizard._get_lots()
        return {
            "doc_ids": wizard.ids, "doc_model": wizard._name, "docs": wizard,
            "lots": lots, "generated_at": fields.Datetime.now(),
            "period_label": dict(wizard._fields["period"]._description_selection(self.env))[wizard.period],
            "status_labels": dict(lots._fields["spx_expiry_status"]._description_selection(self.env)),
            "report_timezone": self.env.context.get("tz") or self.env.user.tz or "UTC",
            "company_names": ", ".join(self.env.companies.mapped("name")),
        }
