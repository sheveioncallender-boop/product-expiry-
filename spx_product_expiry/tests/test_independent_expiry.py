from datetime import timedelta
from copy import deepcopy
from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged, Form
from odoo.tests.common import new_test_user
from odoo.addons.point_of_sale.tests.common import TestPoSCommon


@tagged("post_install", "-at_install")
class TestIndependentExpiry(TestPoSCommon):
    def setUp(self):
        super().setUp()
        self.config = self.basic_config
        self.open_new_session()
        self.stock = self.config.picking_type_id.default_location_src_id
        self.product = self.env["product.product"].create({
            "name": "Expiry medicine", "type": "consu", "is_storable": True,
            "tracking": "none", "spx_expiry_enabled": True, "available_in_pos": True,
            "list_price": 10, "standard_price": 4, "taxes_id": [(5,)],
        })
        self.env["stock.quant"]._update_available_quantity(self.product, self.stock, 23)
        self.Batch = self.env["spx.expiry.batch"]
        today = fields.Date.context_today(self.env.user)
        self.a = self.Batch.create({"product_id": self.product.id, "name": "A", "expiry_date": today + timedelta(days=30), "quantity": 10})
        self.b = self.Batch.create({"product_id": self.product.id, "name": "B", "expiry_date": today + timedelta(days=120), "quantity": 13})

    def _order(self, qty=2, batch=None, original=None, saleable=False, draft=False):
        values = {"product": self.product, "quantity": qty, "spx_expiry_batch_id": (batch or self.a).id}
        if original:
            values.update(refunded_orderline_id=original.id, spx_return_saleable=saleable)
        data = self.create_ui_order_data([values], {"state": "draft" if draft else "paid"})
        self.env["pos.order"].sync_from_ui([deepcopy(data)])
        return self.env["pos.order"].search([("uuid", "=", data["uuid"])]), data

    def test_allocate_without_native_changes(self):
        self.assertEqual(self.product.tracking, "none")
        self.assertEqual(self.product.qty_available, 23)
        self.assertFalse(self.env["stock.lot"].search([("product_id", "=", self.product.id)]))
        self.assertEqual(self.Batch._summary(self.product, self.env.company)["unassigned"], 0)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.b.quantity = 14
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.a.quantity = -1

    def test_pos_sale_and_retry_deduct_once(self):
        order, data = self._order()
        self.assertEqual(self.a.quantity, 8)
        self.assertEqual(self.b.quantity, 13)
        self.assertEqual(self.product.qty_available, 21)
        self.assertTrue(order._spx_stock_completed())
        self.env["pos.order"].sync_from_ui([deepcopy(data)])
        self.assertEqual(self.a.quantity, 8)
        self.assertEqual(self.product.qty_available, 21)
        self.assertEqual(self.env["spx.expiry.movement"].search_count([("pos_line_id", "=", order.lines.id)]), 1)

    def test_draft_does_not_deduct(self):
        self._order(draft=True)
        self.assertEqual(self.a.quantity, 10)
        self.assertEqual(self.product.qty_available, 23)

    def test_two_expiries_and_stale_till_oversell(self):
        self._order(9, self.a)
        self._order(3, self.b)
        with self.assertRaises(UserError), self.cr.savepoint():
            self._order(2, self.a)
        self.assertEqual(self.a.quantity, 1)
        self.assertEqual(self.b.quantity, 10)
        self.assertEqual(self.product.qty_available, 11)

    def test_expired_held_and_wrong_product_rejected(self):
        for values in ({"expiry_date": fields.Date.today() - timedelta(days=2)}, {"saleable": False}):
            original = {key: self.a[key] for key in values}
            self.a.write(values)
            with self.assertRaises(UserError), self.cr.savepoint():
                self._order()
            self.a.write(original)
        other = self.env["product.product"].create({"name": "Other", "is_storable": True, "spx_expiry_enabled": True})
        wrong = self.Batch.create({"product_id": other.id, "expiry_date": self.b.expiry_date})
        with self.assertRaises(UserError), self.cr.savepoint():
            self._order(batch=wrong)

    def test_refund_restores_original_batch(self):
        order, _ = self._order(3, self.b)
        self._order(-2, self.a, original=order.lines, saleable=True)
        self.assertEqual(self.a.quantity, 10)
        self.assertEqual(self.b.quantity, 12)
        self.assertEqual(self.product.qty_available, 22)
        with self.assertRaises(UserError), self.cr.savepoint():
            self._order(-2, original=order.lines, saleable=True)

    def test_refund_hold_and_unlinked_refund(self):
        order, _ = self._order(2)
        self._order(-1, original=order.lines)
        held = self.Batch.search([("product_id", "=", self.product.id), ("saleable", "=", False)])
        self.assertEqual(held.quantity, 1)
        self.assertEqual(self.a.quantity, 8)
        self.assertEqual(self.product.qty_available, 22)
        with self.assertRaises(UserError), self.cr.savepoint():
            self._order(-1)

    def test_cashier_permissions(self):
        cashier = new_test_user(self.env, login="expiry_cashier_test", groups="point_of_sale.group_pos_user")
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.a.with_user(cashier).write({"quantity": 1})
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.env["spx.expiry.movement"].with_user(cashier).create({"batch_id": self.a.id, "delta": 5, "balance": 15, "reason": "Forgery", "user_id": cashier.id})

    def test_company_isolation_and_preflight(self):
        company = self.env["res.company"].create({"name": "Expiry other company"})
        batch = self.Batch.with_company(company).create({"product_id": self.product.id, "company_id": company.id, "expiry_date": self.a.expiry_date})
        with self.assertRaises(UserError), self.cr.savepoint():
            self.config.spx_check_expiry([{"product_id": self.product.id, "qty": 1, "batch_id": batch.id}])
        self.assertTrue(self.config.spx_check_expiry([{"product_id": self.product.id, "qty": 3, "batch_id": self.a.id}]))
        with self.assertRaises(UserError), self.cr.savepoint():
            self.config.spx_check_expiry([{"product_id": self.product.id, "qty": 6, "batch_id": self.a.id}, {"product_id": self.product.id, "qty": 6, "batch_id": self.a.id}])

    def _move(self, qty, source, dest, batch=False, inventory=False):
        move = self.env["stock.move"].create({"product_id": self.product.id, "product_uom": self.product.uom_id.id,
            "product_uom_qty": qty, "location_id": source.id, "location_dest_id": dest.id,
            "company_id": self.env.company.id, "is_inventory": inventory})
        move._action_confirm()
        move.move_line_ids.unlink()
        self.env["stock.move.line"].create({"move_id": move.id, "product_id": self.product.id,
            "product_uom_id": self.product.uom_id.id, "quantity": qty, "location_id": source.id,
            "location_dest_id": dest.id, "spx_expiry_batch_id": batch.id if batch else False})
        move.picked = True
        move._action_done()
        return move

    def test_receipt_delivery_transfer(self):
        self._move(5, self.env.ref("stock.stock_location_suppliers"), self.stock, self.b)
        self.assertEqual(self.b.quantity, 18)
        self.assertEqual(self.product.qty_available, 28)
        customer = self.env.ref("stock.stock_location_customers")
        self._move(2, self.stock, customer, self.a)
        self.assertEqual(self.a.quantity, 8)
        self.assertEqual(self.product.qty_available, 26)
        other = self.env["stock.location"].create({"name": "Other shelf", "usage": "internal", "location_id": self.stock.id})
        self._move(2, self.stock, other)
        self.assertEqual(self.a.quantity + self.b.quantity, 26)
        with self.assertRaises(UserError), self.cr.savepoint():
            self._move(1, self.stock, customer)

    def test_receipt_and_count_reconciliation(self):
        self._move(5, self.env.ref("stock.stock_location_suppliers"), self.stock)
        self.assertEqual(self.Batch._summary(self.product, self.env.company)["unassigned"], 5)
        self._move(6, self.stock, self.product.property_stock_inventory, inventory=True)
        self.assertEqual(self.Batch._summary(self.product, self.env.company)["unassigned"], -1)
        with self.assertRaises(UserError), self.cr.savepoint():
            self._order()
        self.b.quantity = 12
        self.assertEqual(self.Batch._summary(self.product, self.env.company)["unassigned"], 0)

    def test_reports_and_pos_fields(self):
        wizard = self.env["spx.expiry.stock.report"].create({"product_id": self.product.id, "period": "30"})
        self.assertEqual(wizard._batches(), self.a)
        wizard.action_csv()
        self.assertTrue(wizard.file_data)
        html, _ = self.env["ir.actions.report"]._render_qweb_html("spx_product_expiry.report_independent_pdf", wizard.ids)
        self.assertIn(b"Expiry medicine", html)
        self.assertIn("spx.expiry.batch", self.pos_session._load_pos_data_models(self.config))
        self.assertIn("spx_expiry_batch_id", self.env["pos.order.line"]._load_pos_data_fields(self.config))

    def test_product_form_allocation_and_asset_bundle(self):
        self.b.quantity = 12
        with Form(self.product.product_tmpl_id, view="product.product_template_only_form_view") as form:
            with form.spx_independent_batch_ids.new() as batch:
                batch.name = "From product screen"
                batch.expiry_date = self.b.expiry_date
                batch.quantity = 1
        self.assertEqual(self.Batch._summary(self.product, self.env.company)["assigned"], 23)
        bundle = self.env["ir.qweb"]._get_asset_bundle("point_of_sale._assets_pos", css=False, js=True, debug_assets=True)
        template_js = bundle.generate_xml_bundle()
        self.assertNotIn('throw new Error', template_js)
        self.assertIn('spx-expiry-label', template_js)
        javascript = bundle.js().raw.decode()
        self.assertIn('@spx_product_expiry/pos/expiry', javascript)
        for dependency in ["@point_of_sale/app/services/pos_store", "@point_of_sale/app/models/pos_order_line",
                           "@point_of_sale/app/utils/order_payment_validation", "@point_of_sale/app/components/popups/selection_popup/selection_popup"]:
            self.assertIn('odoo.define(' + repr(dependency), javascript)

    def test_completed_line_and_tracking_change_blocked(self):
        order, _ = self._order()
        with self.assertRaises(UserError), self.cr.savepoint():
            order.lines.qty = 7
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.product.tracking = "lot"
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.product.spx_expiry_enabled = False
