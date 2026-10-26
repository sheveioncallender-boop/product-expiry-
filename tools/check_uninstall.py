"""Run inside `odoo shell` against the disposable CI database only."""
import json
import os

assert env.cr.dbname == "spx_expiry_ci", "This check must never run against a live database"
phase = os.environ["SPX_UNINSTALL_PHASE"]
key = "spx_expiry_ci_uninstall_snapshot"
if phase == "prepare":
    product = env["product.product"].create({
        "name": "Uninstall retention check", "type": "consu", "is_storable": True,
        "tracking": "lot", "use_expiration_date": True,
    })
    lot = env["stock.lot"].create({
        "name": "UNINSTALL-KEEP", "product_id": product.id,
        "company_id": env.company.id, "expiration_date": "2030-06-01 16:00:00",
    })
    env["stock.quant"]._update_available_quantity(product, env.ref("stock.stock_location_stock"), 10, lot_id=lot)
    snapshot = {"lot": lot.id, "product": product.id, "expiry": str(lot.expiration_date), "quantity": lot.product_qty}
    env["ir.config_parameter"].set_param(key, json.dumps(snapshot))
    env.cr.commit()
    env["ir.module.module"].search([("name", "=", "spx_product_expiry")]).button_immediate_uninstall()
    print("PASS: expiry addon uninstalled")
elif phase == "verify":
    snapshot = json.loads(env["ir.config_parameter"].get_param(key))
    lot = env["stock.lot"].browse(snapshot["lot"]).exists()
    assert lot and lot.product_id.id == snapshot["product"]
    assert str(lot.expiration_date) == snapshot["expiry"]
    assert lot.product_qty == snapshot["quantity"] == 10
    assert "spx_expiry_status" not in env["stock.lot"]._fields
    assert env["ir.module.module"].search([("name", "=", "product_expiry")]).state == "installed"
    print("PASS: native lots, expiry timestamps and stock balances survive uninstall")
else:
    raise ValueError("Unknown uninstall test phase")
