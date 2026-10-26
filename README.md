# Spxcorp Product Expiry — Odoo 19 Community

A simpler product expiry interface built on Odoo's existing lots, expiry dates and stock balances. Author: **Spxcorp Limited**.

## Included

- Product → General Information → **Product Expiry**: earliest expiry with stock, counts of stocked/missing-date batches, and an editable expiry table for existing batches across all variants.
- **Inventory → Expiry → Expiry Tracking**: product, reference, barcode, batch, expiry, remaining days, status, native on-hand balance and unit.
- Filters for expired, expires today, next 30/60/90 days, missing dates, category and product. Group by product, category, month or company.
- **Inventory → Expiry → Expiry Report**: category/date filters, selected batches, in-stock option, printable PDF and Excel-compatible CSV. Native Odoo list export is also available with existing export permissions.
- **Products Without Expiry Tracking**: a setup review list, not an automatic conversion tool. Non-perishable goods can legitimately remain in this list.
- Expiry edits are recorded in the native batch chatter with the user and change. Existing Inventory/product editing rights continue to apply.

## Native Odoo flow is preserved

This module does not override stock movements, receiving, reservations, quantities, valuation, accounting, POS, returns, stock adjustments or scrap. There is no parallel stock ledger, POS auto-selection, automatic write-off, new alert scheduler or automatic enabling of lot tracking on products. The existing expiry module computes its own removal, best-before and alert dates when its expiration date changes.

All quantities come from **stock.lot.product_qty**. A sale/transfer reduces the batch selected by the native transaction, and a return increases the batch recorded on the return. Staff still need to select and physically pick the correct batch. Existing FEFO settings, POS prompts, expiry warnings and permissions stay in effect.

The dependency is Odoo's official `product_expiry` addon. If it is not already installed, Odoo installs it and performs its standard setup (including enabling the lots feature). Individual product tracking, removal strategies and stock allocations are not changed by this addon.

## Cloudpepper installation

1. Connect repository `sheveioncallender-boop/product-expiry-`, branch **main**.
2. Rebuild the instance so the repository is on its addon path.
3. In Odoo, update the Apps list and install **Spxcorp Product Expiry** (technical name `spx_product_expiry`).
4. Refresh the browser. Open Inventory → Expiry.

The repository contains an addon folder at its root, ready for Cloudpepper. No separate pip dependencies are introduced. PDF generation uses the instance's standard Odoo PDF renderer.

## Existing inventory and daily use

Imported untracked stock has no batch expiry information. Installing the module cannot recover those dates. Review which products require native lot tracking, then allocate their existing physical stock to real batches using a controlled Odoo stock setup process. This module never invents expiry dates or changes opening quantities. Test that setup on a staging copy before applying it to live stock.

Receive new batches using normal Odoo receipts. Enter the manufacturer's actual batch/reference and expiration date there, or correct an existing batch's date on the product screen. Date and time retain Odoo's native meaning and are displayed in the user's timezone (set pharmacy users to America/Port_of_Spain). No time is silently replaced with the end of the day.

**Next Expiry** includes expired batches with positive stock. Empty batches stay visible in the product table for reference; the main tracking menu defaults to in-stock batches. Unset dates are shown separately. Summary figures span all variants of a product. Refresh the screen to update time-based statuses; there is no background browser polling.

Expired means the native expiration timestamp has passed; an already-expired batch earlier today is marked Expired, not Expires Today. Upcoming periods include the rest of today through the end of day 30/60/90 and exclude already-expired batches. Do not interpret a blank date or a date-based status as a clinical safety determination.

Reports use accessible lots and native quantities in the current company/stock context. Quantities from different products/units are deliberately not summed. CSV includes a UTF-8 BOM and escapes formula-like text. When opening CSV in Excel, import barcode/reference columns as text to retain leading zeros. Generate again after changing filters; a downloaded file is a snapshot.

## Access and removal

Menus/report wizard require Inventory User or Administrator. No Accounting or Bank group is granted, and no existing access rule is replaced. Product edits require the user's existing product write access; inventory users can edit a batch's expiry through Manage Expiry Dates using their existing lot rights.

Uninstalling this addon removes its screens, summary fields and transient report records. Native lots, expiry timestamps, stock moves and quantity balances are not owned by it and remain. Do not uninstall the official Odoo Inventory/Products Expiration Date dependencies if those are in use.

## Validation

`python3 tools/check_module.py` checks syntax, manifest resources and the absence of stock/POS workflow overrides. GitHub Actions installs the module in official Odoo 19 with disposable PostgreSQL, runs integration tests, then verifies upgrade. Tests cover native receipts/deliveries/returns, date edits, timezone boundaries, searches, reports, missing dates, duplication, access rights and company isolation. Tests never connect to the pharmacy instance.
