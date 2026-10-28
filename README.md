# Spxcorp Product Expiry — Odoo 19 Community

Version **19.0.2.0.0**. Independent expiry quantities and POS expiry selection by **Spxcorp Limited**.

## What changes

Products can stay on **Track Inventory → By Quantity**. Enable **Independent Expiry** and allocate the stock already in Odoo across expiry rows. Each row contains a product/variant, reference, expiry date and remaining quantity. Allocating expiry quantities does **not** receive stock, change opening balances or create native lots.

Example: Odoo has 23 units. Allocate 10 to expiry A and 13 to expiry B. The expiry total is 23 and Odoo still has 23. A two-unit sale from A leaves A=8, B=13 and Odoo=21.

## Existing stock: simple setup

1. Open **Inventory → Products → Products** and choose a stockable product tracked **By Quantity**.
2. In **General Information**, enable **Independent Expiry** and save.
3. Add rows in **Expiry Stock**, or use **Manage Expiry Stock**. Enter the actual date, remaining quantity and an optional meaningful batch/reference. Choose the correct variant for variant products.
4. Check **Odoo Stock**, **Total Assigned to Expiries** and **Needs Expiry Allocation**. Complete the allocation using the physical stock and packaging dates.

Positive Needs Expiry Allocation means some existing stock is not assigned yet. Negative means assigned quantities exceed Odoo stock and need correction; POS blocks sales of that product until reconciled. The system rejects new allocations above available stock. Reducing an allocation corrects the expiry records only; use normal Inventory for a physical loss or write-off.

Quantities are **company-wide across internal locations**, excluding consignment/third-party-owned stock. This version is suited to the pharmacy's single-store workflow. It does not claim to identify which shelf or warehouse holds a particular expiry. Internal transfers within the company preserve the expiry total.

## POS use

- Scan the ordinary product barcode, or click the product. No separate expiry barcode is needed.
- One eligible expiry: selected automatically. Multiple eligible expiries: choose the date on the item physically being sold. The popup shows date, reference and remaining quantity, earliest first.
- Different expiry selections stay on separate order lines. To sell from two expiries, add a line for each. Increase a line's quantity only within that expiry's availability.
- The selected expiry is shown on the cart/receipt line. Canceling the selection removes the newly-added line.
- Expired entries (before today), held entries and empty entries cannot be sold. An expiry date remains valid through that calendar day; no medical suitability is inferred from the date.
- An online check is required to choose an expiry and validate payment. Final server validation rechecks product, company, date and quantity atomically. Competing tills are serialized and duplicate syncs do not deduct twice.
- If the connection drops during final sync, POS keeps the same pending paid order and shows **Sale awaiting server confirmation**, without displaying a completed receipt. Reconnect and check the order's sync status before handing over goods or taking payment again.
- Odoo performs the native stock deduction. The extension separately deducts the chosen expiry balance, in the same transaction. Expiry orders use native real-time pickings even if the company's ordinary POS stock timing is session-close. A failed native picking prevents completion.
- **Ship Later is not supported** for independently tracked expiry products. Use an Inventory delivery with explicit expiry lines instead.
- Date checks use the company's partner timezone when configured, with **America/Port_of_Spain** as the pharmacy default. Set pharmacy users to that timezone too.

### Refunds

Refund from the **original POS receipt**. The module restores the original expiry allocation, not an arbitrarily selected expiry. The cashier chooses **Hold for review** (default) or **Return to original expiry — confirmed saleable**. Held returns go to a separate held expiry row and cannot be sold. Odoo records the normal physical stock return. Refunds cannot exceed the original quantity sold. Generic negative sale lines without an original expiry sale are rejected.

A refund represents goods returned into inventory. Refunds without a physical return require a separate accounting workflow; do not use this stock-return flow for them.

## New receipts, deliveries and stock counts

- Receive stock through normal Odoo. In **Detailed Operations**, select/create the **Expiry Batch** for each quantity. Create a new entry with **zero** remaining quantity and the actual date; validating the receipt adds its quantity automatically. Split lines for multiple expiries.
- If a receipt has no expiry entry, it completes normally and the quantity appears under **Needs Expiry Allocation**. Staff can then allocate it on the product screen.
- Non-POS deliveries and supplier returns require an explicit expiry batch in Detailed Operations. Their validated quantities reduce that batch. Customer returns with an explicitly chosen batch increase it.
- Stock counts or scrap without a selected expiry remain native stock operations. They create a visible allocation discrepancy; reconcile the expiry rows against the physical count. Negative discrepancies block further POS expiry sales.
- Internal transfers do not change company-wide expiry quantities. Cross-company receipts/deliveries are handled separately in each company.
- Each allocation correction, sale, return and selected stock operation has quantity history. Cashiers have read access to expiry entries, not permission to edit balances or forge history.

## Menus and reports

**Inventory → Expiry** contains:

- **Expiry Tracking**: editable dates/quantities, expired/today/30/60/90-day filters and held stock.
- **Expiry Report**: product/category/date filters, PDF and Excel-compatible CSV.
- **Expiry Products / Reconciliation**: Odoo quantity, assigned quantity and difference per product template (all variants).
- **Products to Set Up for Expiry**: stockable products not yet enabled. Non-perishable products can remain here.
- **Native Lot Expiry (Legacy)**: retains access to the original v1 native-lot interface.

CSV uses a UTF-8 BOM and escapes spreadsheet formula-like text. Import barcode/reference columns as text in Excel to preserve leading zeros. Reports are snapshots; regenerate after changing filters. Unlike products or units are not summed.

## Upgrade in CloudPepper

1. Close/sync all POS orders and close active POS browser tabs.
2. Back up the database. Test the upgrade on a staging copy first.
3. Keep GitHub repository `sheveioncallender-boop/product-expiry-`, branch **main**, and rebuild/redeploy addons in CloudPepper.
4. In Odoo Apps, update the Apps list and **Upgrade Spxcorp Product Expiry** (`spx_product_expiry`). Version 2 adds the official `point_of_sale` dependency and retains `product_expiry`.
5. Reload Odoo and reopen POS so the new assets and fields load.
6. Enable Independent Expiry only on chosen By Quantity products, allocate their real stock, then test one sale and linked refund in staging before live use.

Upgrading does not enable the feature on any product, invent expiry dates, convert stock, or change quantities. Existing native lots, dates, moves and v1 reports remain available. Native lot/serial tracking and Independent Expiry cannot both be enabled on a product.

Uninstalling removes the module's independent expiry records/history and UI; **export those records before uninstalling**. Native stock, POS orders and official lot data remain. The previous v1 promise that all expiry data survives uninstall applied to native lots only; independent data belongs to this module.

## Validation

`python3 tools/check_module.py` validates source/manifest/XML. `node tools/test_pos_expiry.mjs /path/to/odoo/addons/web/static/src/core/utils/patch.js` executes the shipping POS extension with Odoo's patch utility and isolated service fixtures.

GitHub Actions uses a disposable PostgreSQL database and official `odoo:19.0` for actual receipt/delivery/POS/refund/report/access tests, repeated syncs, stock reconciliation, upgrade and uninstall checks. It also tests upgrading the previously published v1 code with existing native and untracked stock. Tests never connect to the pharmacy instance.
