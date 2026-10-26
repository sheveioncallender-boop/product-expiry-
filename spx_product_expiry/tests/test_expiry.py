import base64
import csv
import io
from datetime import datetime, timedelta

from freezegun import freeze_time

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user


@tagged("post_install", "-at_install")
class TestProductExpiry(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tz="America/Port_of_Spain"))
        cls.product = cls.env["product.product"].create({
            "name": "Expiry test product", "type": "consu", "is_storable": True,
            "tracking": "lot", "use_expiration_date": True, "expiration_time": 100,
            "default_code": "SPX-TEST", "barcode": "0123456789012",
        })
        cls.Lot = cls.env["stock.lot"]
        cls.Wizard = cls.env["spx.expiry.report.wizard"]
        cls.stock = cls.env.ref("stock.stock_location_stock")
        cls.supplier = cls.env.ref("stock.stock_location_suppliers")
        cls.customer = cls.env.ref("stock.stock_location_customers")
        cls.stock_user = new_test_user(cls.env, login="spx_expiry_stock_test", groups="stock.group_stock_user", tz="America/Port_of_Spain", context={"no_reset_password": True})
        cls.basic_user = new_test_user(cls.env, login="spx_expiry_basic_test", groups="base.group_user", context={"no_reset_password": True})

    def _lot(self, name, expiry, company=None):
        return self.Lot.create({"name": name, "product_id": self.product.id,
                                "company_id": (company or self.env.company).id,
                                "expiration_date": expiry})

    def _move(self, lot, quantity, source, destination):
        move = self.env["stock.move"].create({
            "product_id": lot.product_id.id,
            "product_uom": lot.product_id.uom_id.id, "product_uom_qty": quantity,
            "location_id": source.id, "location_dest_id": destination.id,
            "company_id": self.env.company.id,
        })
        move._action_confirm()
        # Supplier/customer locations can auto-create an untracked reservation
        # line at confirmation. Replace that draft line with the chosen batch.
        move.move_line_ids.unlink()
        self.env["stock.move.line"].create({
            "move_id": move.id, "product_id": lot.product_id.id,
            "product_uom_id": lot.product_id.uom_id.id, "quantity": quantity,
            "lot_id": lot.id, "location_id": source.id, "location_dest_id": destination.id,
        })
        self.assertEqual(move.quantity, quantity)
        self.assertEqual(move.move_line_ids.lot_id, lot)
        move.picked = True
        move._action_done()
        return move

    @freeze_time("2026-10-26 14:00:00")
    def test_status_boundaries_and_search_agree(self):
        dates = {
            "past": (datetime(2026, 10, 26, 13, 59), "expired"),
            "exact": (datetime(2026, 10, 26, 14), "expired"),
            "today": (datetime(2026, 10, 27, 3, 59), "today"),
            "tomorrow": (datetime(2026, 10, 27, 4), "soon"),
            "last30": (datetime(2026, 11, 26, 3, 59), "soon"),
            "later": (datetime(2026, 11, 26, 4), "later"),
            "missing": (False, "no_date"),
        }
        lots = self.Lot
        for name, (date, state) in dates.items():
            lot = self._lot(name, date)
            self.assertEqual(lot.spx_expiry_status, state)
            lots |= lot
        for state in ("expired", "today", "soon", "later", "no_date"):
            found = self.Lot.search([("id", "in", lots.ids), ("spx_expiry_status", "=", state)])
            expected = lots.filtered(lambda lot: lot.spx_expiry_status == state)
            self.assertEqual(found, expected.sorted("name"))
            excluded = self.Lot.search([("id", "in", lots.ids), ("spx_expiry_status", "!=", state)])
            self.assertEqual(set(excluded.ids), set((lots - expected).ids))

    @freeze_time("2026-10-26 01:00:00")
    def test_timezone_uses_trinidad_day(self):
        lot = self._lot("TZ", datetime(2026, 10, 26, 3, 59))
        self.assertEqual(lot.spx_days_to_expiry, 0)
        self.assertEqual(lot.spx_expiry_status, "today")
        self.assertEqual(self.Lot._spx_day_boundary(fields.Date.to_date("2026-10-26")), datetime(2026, 10, 26, 4))

    def test_native_receipt_delivery_return_and_summary(self):
        first = self._lot("FIRST", fields.Datetime.now() + timedelta(days=20))
        second = self._lot("SECOND", fields.Datetime.now() + timedelta(days=80))
        self._move(first, 20, self.supplier, self.stock)
        self._move(second, 50, self.supplier, self.stock)
        self.assertEqual(first.product_qty, 20)
        self.assertEqual(second.product_qty, 50)
        self.assertEqual(self.product.product_tmpl_id.spx_next_expiration_date, first.expiration_date)
        self._move(first, 3, self.stock, self.customer)
        self.assertEqual(first.product_qty, 17)
        self.assertEqual(second.product_qty, 50)
        self._move(first, 17, self.stock, self.customer)
        self.assertEqual(self.product.product_tmpl_id.spx_next_expiration_date, second.expiration_date)
        self._move(first, 2, self.customer, self.stock)
        self.assertEqual(first.product_qty, 2)
        self.assertEqual(self.product.product_tmpl_id.spx_next_expiration_date, first.expiration_date)

    def test_date_edit_does_not_change_stock_and_is_logged(self):
        lot = self._lot("AUDIT", fields.Datetime.now() + timedelta(days=20))
        self._move(lot, 5, self.supplier, self.stock)
        count = self.env["stock.move"].search_count([])
        removal_before = lot.removal_date
        expiry_before = lot.expiration_date
        lot.with_user(self.stock_user).write({"expiration_date": expiry_before + timedelta(days=4)})
        self.env.flush_all()
        self.env.cr.precommit.run()
        self.assertEqual(lot.product_qty, 5)
        self.assertEqual(self.env["stock.move"].search_count([]), count)
        self.assertEqual(lot.removal_date, removal_before + timedelta(days=4))
        self.assertTrue(lot.message_ids.mapped("tracking_value_ids").filtered(lambda v: v.field_id.name == "expiration_date"))

    def test_missing_dates_and_duplicate_product(self):
        lot = self._lot("NO-DATE", False)
        self._move(lot, 2, self.supplier, self.stock)
        template = self.product.product_tmpl_id
        self.assertEqual(template.spx_undated_lot_count, 1)
        self.assertFalse(template.spx_next_expiration_date)
        copy = template.copy()
        self.assertFalse(copy.spx_expiry_lot_ids)

    def test_report_filters_csv_and_formula_safety(self):
        lot = self._lot("=HYPERLINK(test)", fields.Datetime.now() + timedelta(days=10))
        self._move(lot, 12, self.supplier, self.stock)
        wizard = self.Wizard.create({"period": "30", "lot_ids": [(6, 0, lot.ids)]})
        self.assertEqual(wizard._get_lots(), lot)
        wizard.action_csv()
        rows = list(csv.reader(io.StringIO(base64.b64decode(wizard.file_data).decode("utf-8-sig"))))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][3], "'=HYPERLINK(test)")
        self.assertEqual(rows[1][2], "0123456789012")
        self.assertEqual(float(rows[1][6]), 12)
        self.assertEqual(rows[1][9], "America/Port_of_Spain")

    def test_empty_stock_and_invalid_dates(self):
        lot = self._lot("EMPTY", False)
        wizard = self.Wizard.create({"period": "no_date", "lot_ids": [(6, 0, lot.ids)]})
        with self.assertRaises(UserError):
            wizard._get_lots()
        wizard.in_stock_only = False
        self.assertEqual(wizard._get_lots(), lot)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.Wizard.create({"date_from": "2026-12-01", "date_to": "2026-11-01"})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.Wizard.create({"period": "no_date", "date_to": "2026-11-01"})

    def test_non_inventory_user_cannot_use_reports(self):
        with self.assertRaises(AccessError):
            self.Wizard.with_user(self.basic_user).create({})
        with self.assertRaises(AccessError):
            self.Lot.with_user(self.basic_user).action_spx_expiry_report()
        wizard = self.Wizard.with_user(self.stock_user).create({})
        self.assertEqual(wizard.create_uid, self.stock_user)

    def test_company_isolation(self):
        other_company = self.env["res.company"].create({"name": "Expiry private test company"})
        visible = self._lot("VISIBLE", fields.Datetime.now() + timedelta(days=10))
        hidden = self._lot("OTHER-COMPANY", fields.Datetime.now() + timedelta(days=10), company=other_company)
        wizard = self.Wizard.with_user(self.stock_user).with_context(allowed_company_ids=[self.env.company.id]).create({"in_stock_only": False})
        result = wizard._get_lots()
        self.assertIn(visible, result)
        self.assertNotIn(hidden, result)

    def test_pdf_template_renders_native_balances(self):
        lot = self._lot("REPORT-TEST", fields.Datetime.now() + timedelta(days=20))
        self._move(lot, 7, self.supplier, self.stock)
        wizard = self.Wizard.create({"lot_ids": [(6, 0, lot.ids)]})
        html, _kind = self.env["ir.actions.report"]._render_qweb_html("spx_product_expiry.report_expiry_pdf", wizard.ids)
        self.assertIn(b"REPORT-TEST", html)
        self.assertIn(b"Product Expiry Report", html)
        self.assertIn(b"America/Port_of_Spain", html)

    def test_untracked_products_remain_untracked(self):
        product = self.env["product.product"].create({"name": "Untracked unchanged", "type": "consu", "is_storable": True})
        self.assertEqual(product.tracking, "none")
        self.assertFalse(product.use_expiration_date)
        self.assertFalse(product.product_tmpl_id.spx_expiry_lot_ids)
