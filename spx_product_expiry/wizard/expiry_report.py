import base64
import csv
import io
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class ExpiryReportWizard(models.TransientModel):
    _name = "spx.expiry.report.wizard"
    _description = "Product Expiry Report"

    period = fields.Selection(
        [("all", "All Batches"), ("expired", "Expired"), ("today", "Expires Today"),
         ("30", "Next 30 Days"), ("60", "Next 60 Days"), ("90", "Next 90 Days"),
         ("no_date", "Missing Expiry Date")], required=True, default="all",
    )
    category_id = fields.Many2one("product.category", string="Product Category")
    date_from = fields.Date(string="Expiry From")
    date_to = fields.Date(string="Expiry To")
    in_stock_only = fields.Boolean(string="Only Batches in Stock", default=True)
    lot_ids = fields.Many2many("stock.lot", string="Selected Batches", readonly=True)
    file_data = fields.Binary(string="CSV Report", readonly=True, attachment=False)
    file_name = fields.Char(readonly=True)

    @api.constrains("date_from", "date_to", "period")
    def _check_dates(self):
        for wizard in self:
            if wizard.date_from and wizard.date_to and wizard.date_from > wizard.date_to:
                raise ValidationError(self.env._("Expiry From must not be later than Expiry To."))
            if wizard.period == "no_date" and (wizard.date_from or wizard.date_to):
                raise ValidationError(self.env._("Clear the date range when reporting batches without an expiry date."))

    def _get_lots(self):
        self.ensure_one()
        self.check_access("read")
        Lot = self.env["stock.lot"]
        Lot._spx_check_expiry_access()
        domain = [("product_id.type", "=", "consu"), ("product_id.active", "=", True), ("product_id.tracking", "!=", "none")]
        if self.lot_ids:
            self.lot_ids.check_access("read")
            domain.append(("id", "in", self.lot_ids.ids))
        if self.category_id:
            domain.append(("product_id.categ_id", "child_of", self.category_id.id))
        if self.period in ("expired", "today", "no_date"):
            domain.append(("spx_expiry_status", "=", self.period))
        elif self.period in ("30", "60", "90"):
            now = fields.Datetime.now()
            today = fields.Date.context_today(self, timestamp=now)
            domain += [("expiration_date", ">", now),
                       ("expiration_date", "<", Lot._spx_day_boundary(today + timedelta(days=int(self.period) + 1)))]
        if self.date_from:
            domain.append(("expiration_date", ">=", Lot._spx_day_boundary(self.date_from)))
        if self.date_to:
            domain.append(("expiration_date", "<", Lot._spx_day_boundary(self.date_to + timedelta(days=1))))
        lots = Lot.search(domain, order="expiration_date asc, product_id, name, id")
        # Native quantity search does not honor every location/owner context; use the
        # native computed balance, which does, without manufacturing stock entries.
        if self.in_stock_only:
            lots = lots.filtered(lambda lot: lot.product_qty > 0)
        if not lots:
            raise UserError(self.env._("No batches match these filters."))
        return lots

    def action_preview(self):
        lots = self._get_lots()
        action = self.env["ir.actions.actions"]._for_xml_id("spx_product_expiry.action_expiry_batches")
        action.update(domain=[("id", "in", lots.ids)], context=dict(self.env.context, search_default_in_stock=0))
        return action

    def action_pdf(self):
        self._get_lots()
        return self.env.ref("spx_product_expiry.report_expiry_pdf").report_action(self)

    @staticmethod
    def _csv_text(value):
        text = str(value or "")
        # Keep spreadsheet applications from interpreting product/batch names as formulas.
        return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text

    def action_csv(self):
        lots = self._get_lots()
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["Product", "Internal Reference", "Barcode", "Batch", "Category", "Company",
                         "On Hand", "Unit", "Expiry (local time)", "Timezone", "Days to Expiry", "Status"])
        labels = dict(lots._fields["spx_expiry_status"]._description_selection(self.env))
        zone = self.env.context.get("tz") or self.env.user.tz or "UTC"
        for lot in lots:
            expiry = fields.Datetime.context_timestamp(lot, lot.expiration_date).strftime("%Y-%m-%d %H:%M:%S") if lot.expiration_date else ""
            writer.writerow([
                *map(self._csv_text, [lot.product_id.display_name, lot.spx_reference, lot.spx_barcode,
                                     lot.name, lot.spx_category_id.display_name,
                                     lot.company_id.display_name or self.env._("Shared")]),
                lot.product_qty, self._csv_text(lot.product_uom_id.display_name), expiry, zone,
                lot.spx_days_to_expiry if lot.expiration_date else "", labels[lot.spx_expiry_status],
            ])
        self.write({"file_data": base64.b64encode(output.getvalue().encode("utf-8-sig")),
                    "file_name": "Product_Expiry_%s.csv" % fields.Date.context_today(self)})
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
                "view_mode": "form", "target": "new"}
