# Product incidents

| Date | Incident | Impact | Status |
|---|---|---|---|
| 2026-07-09 | Toast changed its menu and order endpoints. Our sales sync dropped about 30% of orders for Toast customers. | 118 Toast restaurants saw wrong daily sales for up to 9 days. | Hotfixed 2026-07-18 |
| 2026-08-04 | The hotfix missed modifiers and voids. Reconciliation mismatches came back for Toast customers. | About 60 restaurants affected. CS opened 140 tickets in August. | Partial fix 2026-08-20 |
| 2026-09-10 | Duplicate supplier invoices after a Clover update. | 12 restaurants | Fixed 2026-09-12 |

The full fix is a rebuild on Toast's Orders API v2. Estimate: 6 to 8 engineer-weeks. Not started.
