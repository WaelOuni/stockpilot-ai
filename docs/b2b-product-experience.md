# B2B product workflows

The authenticated B2B workspace provides product dialogs, CSV import/export,
comparison of up to four products, a collapsible assistant, French/English interface
copy and light/dark display preferences. Product descriptions and conversation text
are never machine-translated by the interface. Display preferences are the only new
local-storage data; authentication and business data remain outside local storage.

## Product changes and CSV

Organization administrators with `import:execute` can add/edit products. Both forms
use the existing CSV preview/apply workflow and require explicit confirmation.
Changing fields invalidates the preview. No mutation is retried automatically.

The import dialog offers downloadable templates for the existing modes:

| Mode | Columns | Effect |
| --- | --- | --- |
| Physical stock | `sku,quantity` | Replace physical quantity; preserve metadata and reservations |
| Descriptions/prices | `sku,name,description,category,price_minor,currency` | Replace catalogue metadata; preserve physical/reserved stock |
| New products | Previous columns plus `quantity` | Create missing SKUs only; existing SKUs reject the atomic batch |

Use existing catalogue SKUs for updates. Prices in CSV are integer minor currency
units (EUR 49.90 = `4990`); the product dialog accepts decimal prices. Imports keep
the existing 100-row/256-KiB limits and reject physical stock below reservations.

CSV export reads active products of the current organization, optionally filtered
by exact category. It refuses more than 1,000 rows or 4 MiB of cell data rather
than silently truncating. Stock exports additionally require `stock:read`.
Spreadsheet downloads quote cells, use UTF-8 with BOM and prefix formula-like cells
with an apostrophe. That safety prefix is part of the exported value: review it
before reimporting affected SKUs or descriptions.

“Remove” withdraws a product from sale, preserving quantities and historical orders.
It requires explicit confirmation, the observed revision, no outstanding reservation
and `import:execute`. Results are scoped by organization and actor, durable and
idempotent. This is not physical deletion. Reinstatement is not exposed by this UI.

## Upgrade boundary

Migration `0004_b2b_product_archives` adds a dedicated withdrawal-receipt collection.
Migrations 0001–0003 stay byte-for-byte unchanged. Apply through the existing
explicit migration runner with the canonical installation identity checked, after
a verified candidate backup and before opening the new application to users.
No startup migration is performed. Old application versions cannot validate a
newer migration history; rollback requires the corresponding cold candidate backup,
not merely older images. Never apply this procedure to preserved recovery sources.

## Account switching

Every new OIDC login requests `prompt=login`, including when a Keycloak SSO session
survives a local logout. The header displays the authenticated user identity.
“Switch account” closes the BFF session and opens the provider's logout page;
confirm Keycloak sign-out if prompted, then sign in with the intended account.
The ordinary local sign-out remains available without ending unrelated SSO sessions.
Cross-tab account changes invalidate existing views and pending references.
Conversation authorization remains enforced on the server per organization **and
user**, including for managers. Test same-browser user→manager switching as well
as direct access to the previous user's conversation identifier.
