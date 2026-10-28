// Execute the shipping POS extension with Odoo's real patch() implementation.
// Native services are isolated so scan, cancellation and payment edge cases are deterministic.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const patchPath = process.argv[2];
if (!patchPath) throw new Error('Pass the official Odoo 19 web/core/utils/patch.js path');
const patchSource = fs.readFileSync(patchPath, 'utf8').replace('export function patch', 'function patch');
const choices = [];
const dialogs = [];
class PosOrderline {
    constructor(vals) { this.uiState = {}; this.setup(vals); }
    setup(vals) { Object.assign(this, vals); }
    update(vals) { Object.assign(this, vals); }
    canBeMergedWith() { return true; }
}
class PosStore {
    async addLineToOrder(vals, order, opts) {
        const template = typeof vals.product_tmpl_id === 'number' ? this.models['product.template'].get(vals.product_tmpl_id) : vals.product_tmpl_id;
        const line = new PosOrderline({ qty: 1, product_id: template.product_variant_ids[0], ...vals });
        line.qty = opts.code?.value || line.qty;
        order.lines.push(line);
        this.lastOpts = opts;
        return line;
    }
}
class OrderPaymentValidation {
    async isOrderValid() { this.nativeCalled = true; return true; }
}
const context = vm.createContext({ PosStore, PosOrderline, OrderPaymentValidation,
    _t: (s) => s, AlertDialog: class {}, SelectionPopup: class {},
    makeAwaitable: async (dialog, component, props) => { choices.push(props); return context.selection === 'cancel' ? undefined : props.list[context.selection || 0].item; },
});
vm.runInContext(patchSource, context);
vm.runInContext(fs.readFileSync('spx_product_expiry/static/src/pos/expiry.js', 'utf8').replace(/^import .*;\n/gm, ''), context);
const template = { id: 1, spx_expiry_enabled: true, product_variant_ids: [] };
const product = { id: 11, product_tmpl_id: template };
template.product_variant_ids = [product];
const batchA = { id: 21, name: 'A', expiry_date: '2030-12-31', quantity: 10 };
const batchB = { id: 22, name: 'B', expiry_date: '2031-03-31', quantity: 13 };
function fixture(batches = [batchA, batchB]) {
    choices.length = 0; dialogs.length = 0; context.selection = 0;
    const store = new PosStore();
    const batchMap = new Map(batches.map(b => [b.id, b]));
    store.config = { id: 7 };
    store.dialog = { add: (kind, props) => dialogs.push(props) };
    store.models = { 'product.template': { get: () => template }, 'spx.expiry.batch': { get: id => batchMap.get(id) } };
    store.data = { call: async (model, method, args) => {
        store.lastCall = { model, method, args };
        if (method === 'spx_check_expiry') return true;
        return { batches, unassigned: 0 };
    }, read: async () => [] };
    const order = { lines: [], getOrderlines() { return this.lines; }, removeOrderline(line) { this.lines = this.lines.filter(l => l !== line); }, getShippingDate: () => false };
    return { store, order };
}
let f = fixture();
context.selection = 1;
let line = await f.store.addLineToOrder({ product_tmpl_id: 1 }, f.order);
assert.equal(line.spx_expiry_batch_id.id, 22);
assert.equal(choices.length, 1);
assert.equal(f.store.lastOpts.merge, false);
assert.match(line.spx_expiry_label, /2031-03-31/);
let second = await f.store.addLineToOrder({ product_tmpl_id: 1 }, f.order);
assert.equal(f.order.lines.length, 2);
assert.equal(line.canBeMergedWith({ spx_expiry_batch_id: batchA }), false);
assert.equal(line.canBeMergedWith(second), true);
f = fixture([batchA]);
line = await f.store.addLineToOrder({ product_tmpl_id: template }, f.order);
assert.equal(line.spx_expiry_batch_id.id, 21);
assert.equal(choices.length, 0);
f = fixture(); context.selection = 'cancel';
assert.equal(await f.store.addLineToOrder({ product_tmpl_id: template }, f.order), undefined);
assert.equal(f.order.lines.length, 0);
f = fixture([batchA]);
await f.store.addLineToOrder({ product_tmpl_id: template, qty: 9 }, f.order);
assert.equal(await f.store.addLineToOrder({ product_tmpl_id: template, qty: 2 }, f.order), undefined);
assert.equal(f.order.lines.length, 1);
assert.equal(dialogs.length, 1);
f = fixture([batchA]);
f.store.data.call = async () => { throw new Error('Connection unavailable'); };
await f.store.addLineToOrder({ product_tmpl_id: template }, f.order);
assert.equal(f.order.lines.length, 0);
assert.equal(dialogs.length, 1);
// Native refund screen uses model.create directly, not addLineToOrder.
f = fixture();
const refund = new PosOrderline({ product_id: product, qty: -1, refunded_orderline_id: { id: 45, spx_expiry_batch_id: batchB, spx_expiry_label: 'B · 2031-03-31' } });
f.order.lines.push(refund);
assert.equal(refund.spx_expiry_batch_id.id, 22);
let payment = new OrderPaymentValidation(); Object.assign(payment, { pos: f.store, order: f.order });
assert.equal(await payment.isOrderValid(), true);
assert.equal(refund.spx_return_saleable, false);
assert.equal(choices.length, 1);
assert.equal(payment.nativeCalled, true);
assert.equal(f.store.lastCall.args[1][0].refunded_orderline_id, 45);
// Preflight catches changed quantity, server rejection and offline validation.
f = fixture(); f.order.lines.push(new PosOrderline({ product_id: product, qty: 25, spx_expiry_batch_id: batchA }));
f.store.data.call = async () => { throw { data: { message: 'Not enough stock' } }; };
payment = new OrderPaymentValidation(); Object.assign(payment, { pos: f.store, order: f.order });
assert.equal(await payment.isOrderValid(), false);
assert.equal(payment.nativeCalled, undefined);
assert.equal(dialogs[0].body, 'Not enough stock');
console.log('PASS: POS scan choice, single expiry, cancellation, separate lines, cart quantities, offline failure, native refund route and payment validation');
