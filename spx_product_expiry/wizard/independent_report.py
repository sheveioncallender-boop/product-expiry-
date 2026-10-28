import base64
import csv
import io
from datetime import timedelta

from odoo import fields, models
from odoo.exceptions import UserError, ValidationError


class IndependentExpiryReport(models.TransientModel):
    _name = "spx.expiry.stock.report"
    _description = "Independent Expiry Stock Report"

    period = fields.Selection([("all", "All"), ("expired", "Expired"), ("30", "Next 30 Days"),
                               ("60", "Next 60 Days"), ("90", "Next 90 Days"), ("hold", "On Hold")], default="all", required=True)
    category_id = fields.Many2one("product.category")
    product_id = fields.Many2one("product.product")
    date_from = fields.Date()
    date_to = fields.Date()
    in_stock_only = fields.Boolean(default=True)
    file_data = fields.Binary(readonly=True, attachment=False)
    file_name = fields.Char(readonly=True)

    def _batches(self):
        self.ensure_one()
        self.check_access("read")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValidationError(self.env._("From date must not be after To date."))
        domain = []
        today = fields.Date.context_today(self)
        if self.product_id:
            domain.append(("product_id", "=", self.product_id.id))
        if self.category_id:
            domain.append(("product_id.categ_id", "child_of", self.category_id.id))
        if self.in_stock_only:
            domain.append(("quantity", ">", 0))
        if self.period == "expired":
            domain.append(("expiry_date", "<", today))
        elif self.period == "hold":
            domain.append(("saleable", "=", False))
        elif self.period != "all":
            domain += [("expiry_date", ">=", today), ("expiry_date", "<=", today + timedelta(days=int(self.period)))]
        if self.date_from:
            domain.append(("expiry_date", ">=", self.date_from))
        if self.date_to:
            domain.append(("expiry_date", "<=", self.date_to))
        batches = self.env["spx.expiry.batch"].search(domain)
        if not batches:
            raise UserError(self.env._("No expiry entries match these filters."))
        return batches

    def action_preview(self):
        batches = self._batches()
        return {"type": "ir.actions.act_window", "name": "Expiry Stock", "res_model": "spx.expiry.batch",
                "view_mode": "list,form", "domain": [("id", "in", batches.ids)]}

    def action_pdf(self):
        self._batches()
        return self.env.ref("spx_product_expiry.report_independent_pdf").report_action(self)

    def action_csv(self):
        batches = self._batches()
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        safe = self.env["spx.expiry.report.wizard"]._csv_text
        writer.writerow(["Product", "Reference", "Barcode", "Batch", "Expiry Date", "Remaining Quantity", "Unit", "Available for Sale", "Company"])
        for batch in batches:
            writer.writerow([safe(batch.product_id.display_name), safe(batch.product_id.default_code), safe(batch.product_id.barcode),
                             safe(batch.name), str(batch.expiry_date), batch.quantity, safe(batch.uom_id.display_name),
                             "Yes" if batch.saleable else "No", safe(batch.company_id.name)])
        self.write({"file_data": base64.b64encode(stream.getvalue().encode("utf-8-sig")), "file_name": "Expiry_Stock.csv"})
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id, "view_mode": "form", "target": "new"}
