# Provider integration research

Research date: **2026-10-16**. Czech consumer accounts only. Public documentation
and source-code review; no authenticated tests for these five providers. No
credentials collected. Data retention, user-visible history and API availability
are different claims; none implies the others.

| Provider | Receipt/order data | Integration candidate | Next priority |
| --- | --- | --- | --- |
| Rohlík | Order history and PDF invoices confirmed | Official OAuth MCP; unofficial Python client | 1 |
| Globus | Receipt history in app **and web** confirmed | Inspect authenticated web API | 2 |
| Košík | Delivered order details and profile tax documents | Official MCP capability discovery, then profile API | 3 |
| Albert CZ | Digital receipt archive confirmed | Inspect Czech app/API after service restoration | 4 |
| BILLA CZ | Purchase data processing confirmed; receipt export unproven | Verify current Czech app capabilities first | 5 |

Lidl is already experimentally verified separately. These priorities concern
additional providers; no connector below is implemented in the application.

## Rohlík

- [Official MCP documentation](https://www.rohlik.cz/en-CZ/mcp-docs):
  `https://mcp.rohlik.cz/mcp`, OAuth browser sign-in and order history are
  documented. Prefer this supported route for the first authenticated test.
  Verify tool schemas, final delivered quantities/prices, discounts, order
  identifiers, paging, retention and whether unattended refresh is permitted.
  Shopping tools can modify the real cart: the importer must allow only reads.
- [Official FAQ](https://www.rohlik.cz/stranka/caste-dotazy): invoices can be
  downloaded as PDF from account order history; actual charged prices reflect
  delivered weights. Do not treat basket estimates as final spend.
- [Unofficial Python client](https://github.com/dvejsada/rohlik_api_python),
  [PyPI](https://pypi.org/project/rohlik-api/): version 0.3.0, Python >=3.13,
  async HTTP client; email/password login, delivered orders with `limit/offset`,
  order details and raw JSON. Its bulk helper can return an accumulated partial
  list after a failed page; `None` must not mean an empty successful page.
  Wrap with explicit completeness/error checks before use. No live validation.
- Exact archive length, invoice corrections and discount representation remain
  unknown. Official MCP order support does not prove every field needed by
  Cartalytix is exposed.

## Globus

- [Program conditions, effective 2025-08-13](https://www.globus.cz/o-nas/dokumenty-vernostniho-programu/vseobecne-podminky):
  receipt overview exists on the web and in Můj Globus. The loyalty card must
  be scanned before the checkout operation. Choosing paper receipts does not
  remove digital history. This makes web-session inspection a practical next
  experiment without needing mobile-app reverse engineering first.
- [Privacy information](https://www.globus.cz/o-nas/dokumenty-vernostniho-programu/informace-o-ochrane-a-zpracovani-osobnich-udaju):
  describes purchase-level and item-level data including product identifier,
  quantity, price, store and time, with a maximum two-year processing period
  for the relevant data. This is **not a guarantee of a two-year API archive**.
- [Receipt announcement from 2022](https://www.globus.cz/o-nas/tiskove-zpravy/nova-uctenka-globus):
  historical information about sharing receipts and availability delay; do not
  assume the old delay or sharing format remains current.
- No verified Czech public receipt API/client was found. This does not prove
  none exists. Auth token shape, refresh, MFA, pagination, stable receipt IDs,
  item discounts and correction handling need an authenticated experiment.

## Košík

- [Official MCP](https://www.kosik.cz/stranky/mcp-server):
  `https://mcp.kosik.cz/mcp/`, browser sign-in, product search, cart and checkout
  documented. Order-history/receipt retrieval is **not documented there**;
  inspect available read-only tools after authorization before selecting it.
  Never invoke checkout/cart mutation tools as part of import.
- [Official conditions](https://www.kosik.cz/stranky/obchodni-podminky),
  [FAQ](https://www.kosik.cz/stranky/caste-dotazy): final delivered goods,
  quantities and tax documents are available through the customer profile.
  The importer must distinguish ordered, substituted and delivered items and
  account for refunds, packaging and delivery charges.
- No verified Czech receipt client found. Public web login alone does not
  establish an OAuth/refresh protocol. History depth, paging, discount fields,
  download format and stable document IDs remain unverified.

## Albert CZ

- [Můj Albert](https://www.albert.cz/aplikace) advertises digital receipts;
  [official receipt announcement](https://www.albert.cz/news-room/tiskove_zpravy_cele/elektronicke-uctenky-maji-nahradit-papirove)
  describes their archive. No precise current archive limit was established.
  Account identifiers include phone/email, but registration or phone-change
  SMS does not establish the actual login/token protocol.
- [Current official service notice](https://www.albert.cz/news-room/tiskove_zpravy_cele/aktualni-upozorneni),
  dated 2026-10-13: Můj Albert is unavailable; points/credits are to remain
  available after restoration. Recheck service state before testing.
- [Privacy policy](https://www.albert.cz/zasady-zpracovani-osobnich-udaju):
  account inactivity/deletion periods must not be presented as receipt-history
  guarantees.
- No verified Czech client found. Albert Heijn/Appie clients target a different
  country/service and are not evidence of compatibility with Albert CZ.
  Receipt sharing is a fallback to verify, not a proven bulk export/API.

## BILLA CZ

- [Registration](https://www.billa.cz/registrace),
  [password recovery](https://www.billa.cz/zapomenute-heslo): Czech account
  uses email/password. No supported receipt OAuth/API was established.
- [Privacy policy, section 10.2](https://www.billa.cz/ochrana-osobnich-udaju):
  purchase history and item-level processing are described, with a maximum
  three-year period for relevant data. This establishes internal processing,
  **not a three-year downloadable receipt archive**.
- [Czech app page](https://www.billa.cz/billa-klub/aplikace) does not establish
  receipt export. Historical developer replies in the
  [Czech App Store listing](https://apps.apple.com/cz/app/billa-%C4%8Desk%C3%A1-republika/id599578438?platform=iphone&see-all=reviews)
  described receipts as planned in 2025; that is not current feature proof.
- [Current online shopping page](https://www.billa.cz/online-nakup) points to
  delivery partners. Old BILLA e-shop invoice terms do not prove access to
  physical-store receipts or partner-order data through BILLA Klub.
- First verify whether the current Czech app exposes full receipts. Then
  inspect auth and list/detail endpoints, sharing, history and paging.

## Acceptance criteria for the next account experiment

1. User signs in locally/browser; no passwords or tokens in chat, Git or logs.
2. Save redacted protocol/schema notes and synthetic fixtures; keep real
   responses and identifiers ignored locally.
3. Fetch every page with explicit completeness counters; distinguish errors,
   expired auth, empty history and genuinely completed pagination.
4. Compare one final document to visible receipt/invoice: item names, units,
   quantities, gross prices, discounts, deposits, fees, refunds and total.
5. Verify oldest available record and stable IDs; do not infer archive length
   from privacy retention. Repeat recent overlap to detect corrections.
6. Test refresh and incremental cursor/updated-at support. If absent, use a
   bounded recent overlap with idempotent upserts and periodic deeper checks.
7. Record rate limits and failure/re-auth behavior. Private endpoints can
   change without notice; a successful experiment is not an API contract.
