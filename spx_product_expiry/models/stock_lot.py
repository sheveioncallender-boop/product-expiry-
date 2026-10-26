from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models
from odoo.exceptions import AccessError
from odoo.fields import Domain


class StockLot(models.Model):
    _inherit = "stock.lot"

    # Extend the existing field: the native expiry/removal/alert computations remain intact.
    expiration_date = fields.Datetime(tracking=True)
    spx_product_tmpl_id = fields.Many2one(
        related="product_id.product_tmpl_id", store=True, index=True,
    )
    spx_category_id = fields.Many2one(related="product_id.categ_id", store=True)
    spx_reference = fields.Char(related="product_id.default_code", string="Product Reference")
    spx_barcode = fields.Char(related="product_id.barcode", string="Barcode")
    spx_expiry_status = fields.Selection(
        [("expired", "Expired"), ("today", "Expires Today"),
         ("soon", "Within 30 Days"), ("later", "More Than 30 Days"),
         ("no_date", "No Expiry Date")],
        string="Expiry Status", compute="_compute_spx_expiry",
        search="_search_spx_expiry_status",
    )
    spx_days_to_expiry = fields.Integer(string="Days to Expiry", compute="_compute_spx_expiry")

    @api.model
    def _spx_check_expiry_access(self):
        if not self.env.user.has_group("stock.group_stock_user"):
            raise AccessError(self.env._("Inventory access is required to use expiry tracking."))

    @api.model
    def _spx_day_boundary(self, day):
        """Local midnight expressed as the naive UTC datetime used by Odoo."""
        zone = pytz.timezone(self.env.context.get("tz") or self.env.user.tz or "UTC")
        return zone.localize(datetime.combine(day, time.min)).astimezone(pytz.UTC).replace(tzinfo=None)

    @api.depends("expiration_date")
    @api.depends_context("tz", "uid")
    def _compute_spx_expiry(self):
        now = fields.Datetime.now()
        today = fields.Date.context_today(self, timestamp=now)
        for lot in self:
            expiry = lot.expiration_date
            if not expiry:
                lot.spx_expiry_status = "no_date"
                lot.spx_days_to_expiry = 0
                continue
            days = (fields.Datetime.context_timestamp(lot, expiry).date() - today).days
            lot.spx_days_to_expiry = days
            lot.spx_expiry_status = (
                "expired" if expiry <= now else
                "today" if days == 0 else
                "soon" if days <= 30 else "later"
            )

    @api.model
    def _search_spx_expiry_status(self, operator, value):
        if operator not in ("=", "!=", "in", "not in"):
            return NotImplemented
        values = [value] if operator in ("=", "!=") else value
        now = fields.Datetime.now()
        today = fields.Date.context_today(self, timestamp=now)
        tomorrow = self._spx_day_boundary(today + timedelta(days=1))
        end_30 = self._spx_day_boundary(today + timedelta(days=31))
        domains = {
            "expired": Domain("expiration_date", "<=", now),
            "today": Domain("expiration_date", ">", now) & Domain("expiration_date", "<", tomorrow),
            "soon": Domain("expiration_date", ">=", tomorrow) & Domain("expiration_date", "<", end_30),
            "later": Domain("expiration_date", ">=", end_30),
            "no_date": Domain("expiration_date", "=", False),
        }
        result = Domain.OR([domains.get(v, Domain.FALSE) for v in values])
        return ~result if operator in ("!=", "not in") else result

    def action_spx_expiry_report(self):
        self._spx_check_expiry_access()
        self.check_access("read")
        action = self.env["ir.actions.actions"]._for_xml_id("spx_product_expiry.action_expiry_report_wizard")
        action["context"] = dict(self.env.context, default_lot_ids=[(6, 0, self.ids)])
        return action

    def action_spx_open_lot(self):
        self.ensure_one()
        self._spx_check_expiry_access()
        self.check_access("read")
        return {
            "type": "ir.actions.act_window", "name": self.display_name,
            "res_model": "stock.lot", "res_id": self.id, "view_mode": "form",
            "views": [(self.env.ref("stock.view_production_lot_form").id, "form")],
            "target": "current",
        }
