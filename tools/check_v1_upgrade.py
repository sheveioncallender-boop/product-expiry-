"""Run only against the disposable CI upgrade database, in two processes."""
import os
import json
assert env.cr.dbname == 'spx_expiry_upgrade'
key = 'spx_expiry_upgrade_snapshot'
if os.environ['SPX_UPGRADE_PHASE'] == 'prepare':
    product = env['product.product'].create({'name': 'Existing quantity-only stock', 'is_storable': True, 'tracking': 'none'})
    native = env['product.product'].create({'name': 'Existing native lots', 'is_storable': True, 'tracking': 'lot', 'use_expiration_date': True})
    lot = env['stock.lot'].create({'name': 'KEEP-NATIVE', 'product_id': native.id, 'company_id': env.company.id, 'expiration_date': '2030-12-31 16:00:00'})
    stock = env.ref('stock.stock_location_stock')
    env['stock.quant']._update_available_quantity(product, stock, 23)
    env['stock.quant']._update_available_quantity(native, stock, 7, lot_id=lot)
    env['ir.config_parameter'].set_param(key, json.dumps({'product': product.id, 'native': native.id, 'lot': lot.id}))
    env.cr.commit()
    print('PASS: v1 stock fixture saved')
else:
    snapshot = json.loads(env['ir.config_parameter'].get_param(key))
    product = env['product.product'].browse(snapshot['product'])
    native = env['product.product'].browse(snapshot['native'])
    lot = env['stock.lot'].browse(snapshot['lot'])
    assert product.tracking == 'none' and product.qty_available == 23 and not product.spx_expiry_enabled
    assert native.tracking == 'lot' and lot.product_qty == 7
    assert str(lot.expiration_date) == '2030-12-31 16:00:00'
    assert not env['spx.expiry.batch'].search_count([])
    assert env.ref('spx_product_expiry.menu_expiry_batches').action.res_model == 'spx.expiry.batch'
    assert env['ir.module.module'].search([('name', '=', 'point_of_sale')]).state == 'installed'
    print('PASS: v1 to v2 upgrade preserves native stock/dates and leaves independent tracking opt-in')
