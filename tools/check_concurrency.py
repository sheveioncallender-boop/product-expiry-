"""Exercise simultaneous first allocations in real REPEATABLE READ transactions."""
import threading
from odoo import api, SUPERUSER_ID, fields
from odoo.exceptions import ValidationError
from psycopg2.errors import SerializationFailure
assert env.cr.dbname == 'spx_expiry_ci'
product = env['product.product'].create({'name': 'Concurrent allocation check', 'is_storable': True, 'spx_expiry_enabled': True})
stock = env.ref('stock.stock_location_stock')
env['stock.quant']._update_available_quantity(product, stock, 10)
product_id, company_id = product.id, env.company.id
env.cr.commit()
barrier = threading.Barrier(2)
results = []

def allocate():
    try:
        with env.registry.cursor() as cr:
            thread_env = api.Environment(cr, SUPERUSER_ID, {'allowed_company_ids': [company_id]})
            # Establish both snapshots before either allocation updates the product row.
            thread_env['product.product'].browse(product_id).read(['name'])
            barrier.wait(timeout=20)
            thread_env['spx.expiry.batch'].create({'product_id': product_id, 'company_id': company_id,
                'name': 'Simultaneous entry', 'expiry_date': '2035-12-31', 'quantity': 7})
            cr.commit()
            results.append('accepted')
    except SerializationFailure:
        # This is the retry Odoo's HTTP service performs. The fresh transaction
        # must now see the winning allocation and reject the excess quantity.
        with env.registry.cursor() as cr:
            thread_env = api.Environment(cr, SUPERUSER_ID, {'allowed_company_ids': [company_id]})
            try:
                thread_env['spx.expiry.batch'].create({'product_id': product_id, 'company_id': company_id,
                    'expiry_date': '2035-12-31', 'quantity': 7})
            except ValidationError:
                results.append('rejected_after_retry')
            else:
                results.append('incorrectly_accepted_retry')
            cr.rollback()
    except Exception as exc:
        results.append(type(exc).__name__ + ': ' + str(exc))

threads = [threading.Thread(target=allocate) for _ in range(2)]
for thread in threads: thread.start()
for thread in threads: thread.join(timeout=40)
assert sorted(results) == ['accepted', 'rejected_after_retry'], results
env.cr.rollback()
env.invalidate_all()
assert sum(env['spx.expiry.batch'].search([('product_id', '=', product_id)]).mapped('quantity')) == 7
print('PASS: competing first allocations cannot exceed stock, including REPEATABLE READ retry')
