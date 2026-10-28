import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { ConnectionLostError, RPCError } from "@web/core/network/rpc";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { PosOrderline } from "@point_of_sale/app/models/pos_order_line";
import { SelectionPopup } from "@point_of_sale/app/components/popups/selection_popup/selection_popup";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import OrderPaymentValidation from "@point_of_sale/app/utils/order_payment_validation";

patch(PosStore.prototype, {
    async addLineToOrder(vals, order, opts = {}, configure = true) {
        const template = typeof vals.product_tmpl_id === "number"
            ? this.models["product.template"].get(vals.product_tmpl_id) : vals.product_tmpl_id;
        if (!template?.spx_expiry_enabled) {
            return await super.addLineToOrder(vals, order, opts, configure);
        }
        // Let native configuration resolve the actual variant and scanned quantity.
        // Never merge before the cashier has chosen an expiry.
        const line = await super.addLineToOrder(vals, order, { ...opts, merge: false }, configure);
        if (!line) {
            return line;
        }
        try {
            if (line.qty < 0) {
                const original = line.refunded_orderline_id;
                if (!original?.spx_expiry_batch_id) {
                    throw new Error(_t("Use the original receipt to refund expiry products."));
                }
                const disposition = await makeAwaitable(this.dialog, SelectionPopup, {
                    title: _t("Returned goods: where should the expiry quantity go?"),
                    list: [
                        { id: "hold", label: _t("Hold for review — not available for sale"), item: "hold" },
                        { id: "sale", label: _t("Return to original expiry — confirmed saleable"), item: "sale" },
                    ],
                });
                if (!disposition) {
                    order.removeOrderline(line);
                    return;
                }
                line.update({ spx_expiry_batch_id: original.spx_expiry_batch_id,
                    spx_expiry_label: original.spx_expiry_label,
                    spx_return_saleable: disposition === "sale" });
                line.uiState.spxReturnReviewed = true;
                return line;
            }
            const response = await this.data.call("spx.expiry.batch", "pos_choices", [line.product_id.id, this.config.id]);
            if (response.unassigned < -0.000001) {
                throw new Error(_t("Expiry quantities exceed Odoo stock. Ask Inventory to reconcile this product."));
            }
            const choices = response.batches.map((batch) => {
                const inCart = order.getOrderlines().filter((other) => other !== line && other.qty > 0 && other.spx_expiry_batch_id?.id === batch.id)
                    .reduce((sum, other) => sum + other.qty, 0);
                return { ...batch, available: batch.quantity - inCart };
            }).filter((batch) => batch.available >= line.qty - 0.000001);
            if (!choices.length) {
                throw new Error(_t("No expiry batch has enough saleable stock for this quantity. Assign stock in Inventory, or add separate lines for each expiry."));
            }
            const choice = choices.length === 1 ? choices[0] : await makeAwaitable(this.dialog, SelectionPopup, {
                title: _t("Select the expiry on the item being sold"),
                list: choices.map((batch) => ({ id: batch.id,
                    label: `${batch.expiry_date} · ${batch.name} · ${batch.available} available`, item: batch })),
            });
            if (!choice) {
                order.removeOrderline(line);
                return;
            }
            // read() also inserts newly-created batches into the relational POS store.
            await this.data.read("spx.expiry.batch", [choice.id]);
            line.update({ spx_expiry_batch_id: this.models["spx.expiry.batch"].get(choice.id),
                spx_expiry_label: `${choice.name} · ${choice.expiry_date}` });
            return line;
        } catch (error) {
            order.removeOrderline(line);
            this.dialog.add(AlertDialog, {
                title: _t("Expiry selection required"),
                body: error.data?.message || error.message || _t("Connect to the server to check expiry availability."),
            });
            return;
        }
    },
});

patch(PosOrderline.prototype, {
    setup(vals) {
        super.setup(vals);
        // The native receipt-refund screen creates lines directly, bypassing
        // PosStore.addLineToOrder. Carry the original batch through that route too.
        if (this.qty < 0 && this.refunded_orderline_id?.spx_expiry_batch_id && !this.spx_expiry_batch_id) {
            this.update({ spx_expiry_batch_id: this.refunded_orderline_id.spx_expiry_batch_id,
                spx_expiry_label: this.refunded_orderline_id.spx_expiry_label });
        }
    },
    canBeMergedWith(other) {
        return (this.spx_expiry_batch_id?.id || false) === (other.spx_expiry_batch_id?.id || false)
            && super.canBeMergedWith(other);
    },
});

patch(OrderPaymentValidation.prototype, {
    spxHasExpiryLines() {
        return this.order.getOrderlines().some((line) => line.product_id.product_tmpl_id.spx_expiry_enabled);
    },
    async shouldHideValidationBehindFeedbackScreen() {
        if (!this.spxHasExpiryLines()) {
            return await super.shouldHideValidationBehindFeedbackScreen();
        }
        // Await the server even when native POS uses its quick feedback screen.
        // A pending offline order must not look like an accepted expiry sale.
        this.pos.env.services.ui.block();
        try {
            const result = await this.finalizeValidation();
            if (result === false || result instanceof ConnectionLostError || result instanceof RPCError) {
                return false;
            }
            const next = this.nextPage;
            this.pos.navigate(next.page, next.params);
        } finally {
            this.pos.env.services.ui.unblock();
        }
    },
    handleValidationError(error) {
        if (this.spxHasExpiryLines() && error instanceof ConnectionLostError) {
            // Keep the same paid order/UUID for native retry: the server may
            // already have committed before its response was lost.
            this.pos.data.synchronizeLocalDataInIndexedDB();
            this.pos.dialog.add(AlertDialog, {
                title: _t("Sale awaiting server confirmation"),
                body: _t("The connection dropped. Keep this order and reconnect to sync it. Check its status before handing over goods or taking payment again."),
            });
            return error;
        }
        return super.handleValidationError(error);
    },
    async isOrderValid(isForceValidate) {
        const lines = this.order.getOrderlines().filter((line) => line.product_id.product_tmpl_id.spx_expiry_enabled);
        if (lines.length) {
            try {
                for (const line of lines.filter((item) => item.qty < 0 && !item.uiState.spxReturnReviewed)) {
                    const original = line.refunded_orderline_id;
                    if (!original?.spx_expiry_batch_id) {
                        throw new Error(_t("Use the original receipt to refund expiry products."));
                    }
                    const disposition = await makeAwaitable(this.pos.dialog, SelectionPopup, {
                        title: _t("Returned goods: where should the expiry quantity go?"),
                        list: [
                            { id: "hold", label: _t("Hold for review — not available for sale"), item: "hold" },
                            { id: "sale", label: _t("Return to original expiry — confirmed saleable"), item: "sale" },
                        ],
                    });
                    if (!disposition) { return false; }
                    line.update({ spx_expiry_batch_id: original.spx_expiry_batch_id,
                        spx_expiry_label: original.spx_expiry_label, spx_return_saleable: disposition === "sale" });
                    line.uiState.spxReturnReviewed = true;
                }
                if (this.order.getShippingDate()) {
                    throw new Error(_t("Expiry products must be handed over at the till; remove Ship Later."));
                }
                await this.pos.data.call("pos.config", "spx_check_expiry", [[this.pos.config.id], lines.map((line) => ({
                    product_id: line.product_id.id, qty: line.qty,
                    batch_id: line.spx_expiry_batch_id?.id || false,
                    refunded_orderline_id: line.refunded_orderline_id?.id || false,
                }))]);
            } catch (error) {
                this.pos.dialog.add(AlertDialog, { title: _t("Check expiry stock"),
                    body: error.data?.message || error.message || _t("An online expiry check is required before payment validation.") });
                return false;
            }
        }
        return await super.isOrderValid(isForceValidate);
    },
});
