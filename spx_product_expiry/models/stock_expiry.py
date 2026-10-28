from odoo import fields, models
from odoo.exceptions import UserError


class StockMoveLine(models.Model):
    _inherit = "stock.move.line"

    spx_expiry_batch_id = fields.Many2one("spx.expiry.batch", string="Expiry Batch", check_company=True, copy=False)
    spx_expiry_enabled = fields.Boolean(related="product_id.spx_expiry_enabled")


class StockMove(models.Model):
    _inherit = "stock.move"

    def _action_done(self, cancel_backorder=False):
        candidates = self.filtered(lambda m: m.state not in ("done", "cancel"))
        Batch = self.env["spx.expiry.batch"]
        Batch._lock_products(candidates.product_id.filtered("spx_expiry_enabled"))
        result = super()._action_done(cancel_backorder=cancel_backorder)
        Movement = self.env["spx.expiry.movement"].sudo()
        for line in result.filtered(lambda m: m.state == "done").move_line_ids:
            if not line.product_id.spx_expiry_enabled or line.picking_id.pos_session_id or not line.quantity:
                continue
            if Movement.search_count([("move_line_id", "=", line.id)]):
                continue
            incoming = line.location_dest_id.usage == "internal"
            outgoing = line.location_id.usage == "internal"
            if incoming == outgoing:
                continue  # Company-wide expiry buckets: internal transfers conserve quantities.
            batch = line.spx_expiry_batch_id
            if not batch:
                if outgoing and not line.move_id.is_inventory and not line.move_id.scrap_id:
                    raise UserError(self.env._("Choose an Expiry Batch in Detailed Operations for %s. Split lines when using multiple expiries.", line.product_id.display_name))
                # Receipts without dates and stock counts appear as allocation differences.
                continue
            if batch.product_id != line.product_id or batch.company_id != line.company_id or line.owner_id:
                raise UserError(self.env._("The expiry batch must match the product and company and cannot be used for consignment stock."))
            quantity = line.product_uom_id._compute_quantity(line.quantity, line.product_id.uom_id)
            batch._change_quantity(quantity if incoming else -quantity, line.reference or "Stock movement", move_line=line)
        return result
