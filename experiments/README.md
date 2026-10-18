# Cartalytix — experiment Lidl Plus CZ

Minimální Python/uv projekt pro místní export vlastních účtenek. Bez dashboardu,
databáze a plánovače. Neoficiální API, bez garance dostupnosti nebo stability.

Živý experiment uspěl: 861 účtenek, 4 836 položek, 596 slev; audit všech součtů
prošel. Rozsah a limity důkazu: [docs/experiment.md](docs/experiment.md).

## Spuštění ve Windows

Vyžaduje `uv` a nainstalovaný Chrome. Připraveno pro Python 3.12.

```powershell
uv sync --locked
uv run --locked cartalytix login --browser chrome
uv run --locked cartalytix export
```

Alternativně `./scripts/login.ps1` a `./scripts/export.ps1`.
Příkaz `uv run --locked cartalytix probe --browser chrome` spojí přihlášení
(pokud token chybí) a export. Edge: `--browser msedge`.
Chromium: `uv run playwright install chromium`, pak `--browser chromium`.

**Přihlašovací jméno, heslo a SMS/e-mail kód zadávejte pouze v nově otevřeném
okně Lidl na accounts.lidl.com. Nic neposílejte do chatu.**
Nový dočasný profil prohlížeče nepoužívá ani nečte váš běžný profil.
Neukládáme cookies, hesla, screenshoty ani síťové záznamy přihlášení.
Případnou CAPTCHA nebo nové podmínky řeší ručně uživatel přímo na webu.

OAuth2 Authorization Code + PKCE/S256 ověřuje návratový `state`. Refresh token
se ukládá výhradně do Windows Credential Manageru (`Cartalytix.LidlPlus`,
`CZ:default`), bez nešifrovaného fallbacku. Access token zůstává v paměti.
Rotovaný refresh token se ukládá před dalším API požadavkem. Nejistý token POST
se automaticky neopakuje. Při selhání/expiraci spusťte login znovu.
Počítač a jeho uživatelský účet musí zůstat důvěryhodné.

Každé přihlášení založí nový náhodný exportní prostor; zabrání tím smíchání
účtenek různých účtů. Původní exporty zůstávají. Pro běžné opakování stačí
`export`, nové přihlášení není potřeba.

## Výstupy

Pod `data/CZ/<lokální-profil>/`:

- `index.json`: úplný seznam účtenek posledního úspěšného průchodu seznamem.
- `raw/pages/<běh>/`: původní stránkované odpovědi včetně metadat.
- `raw/details/`: původní detaily, názvy souborů = SHA-256 ID účtenky.
- `receipts.json`: normalizovaný poslední kompletní export, ID, datum, měna,
  položky, množství, zdrojové jednotkové a řádkové ceny, slevy a součty.
- `receipts.partial.json`: neúplný pokus; nenahrazuje poslední kompletní export.
- `report.json`: počty, stránky, nejstarší/nejnovější datum, duplicity,
  chyby a souhrn kvality posledního pokusu.
- `quality.json`: podrobný offline audit po spuštění `scripts/check_export.py`.

Data jsou soukromá, na disku v čitelném JSON; `.gitignore` vylučuje `data/`,
`work/`, tokeny i cookies. **Exporty mohou obsahovat údaje o prodejně, platbě a
věrnostní kartě.** Nesdílejte raw data veřejně. Skript je neposílá analytické službě.
Nezálohujte exporty do veřejného úložiště. Žádné automatické mazání dat.

## Úplnost, stránkování, opakování

Seznam: `GET https://tickets.lidlplus.com/api/v2/CZ/tickets`,
`pageNumber=1..N`, `onlyFavorite=false`. Detail: `GET /api/v3/CZ/tickets/{id}`.
Živě ověřeno: seznam akceptuje `Accept-Language: cs-CZ`, detail potřebuje `cs`;
detail s `cs-CZ` vracel HTTP 400. Nejde tedy jen o záměnu čísla verze endpointu.
Velikost stránky se pouze zaznamenává, netvrdíme pevnou hodnotu 25.
Krátká stránka sama o sobě neukončí průchod. Při známém `totalCount` musí sedět
počet unikátních ID; bez něj pokračujeme až po prázdnou stránku.
Opakované/překryté stránky, změna `totalCount` nebo první stránky, limit stránek
a prázdná stránka před očekávaným koncem znamenají neúplný běh a nenulový exit.
API neposkytuje zaručený snapshot: kontrola první stránky zachytí běžné změny,
ne všechny souběžné změny historie. Export je vhodné zopakovat mimo nákup.

Výchozí limit 10 000 stránek je pojistka, ne limit historie. `--max-pages N`
umožní řízený malý pokus; dosažení limitu se nikdy nevydává za úplnou historii.
Požadavky běží postupně, s prodlevou 0,5 s, timeoutem 30 s a omezenými retry GET
pro síť/429/5xx. Dlouhý nebo neurčitý `Retry-After` export zastaví, aby se limit
neobcházel. Žádné automatické obcházení CAPTCHA, vypínání TLS nebo aktivování kupónů.
Po třech po sobě jdoucích chybách detailu se celý běh zastaví, aby se stejná
systémová chyba neopakovala pro všechny účtenky. Sanitizovaný důvod zůstane v reportu.

Opakovaný `export` projde znovu celý seznam, ale stáhne jen chybějící detaily.
To pokryje i opožděně přidanou starší účtenku; neukončuje se při prvním známém ID.
Vadný cache JSON se stáhne znovu. Raw detaily zůstávají zachované i tehdy, když
účtenka později zmizí z viditelné historie; aktuální export obsahuje aktuální seznam.
Pro opravy a vratky stávajících účtenek použijte pravidelně:

```powershell
uv run --locked cartalytix export --refresh-details
```

Čistý delta endpoint/cursor ani stabilní řazení podle času nebyly potvrzeny.
Pro budoucí synchronizaci je tedy nyní konzervativní základ ID + cache detailů
+ úplný průchod seznamem + občasné obnovení všech detailů.
Spouštějte jen jeden export/login současně: souběžná obnova může zneplatnit token.

## Kontrola kvality

`complete=true` znamená dokončený přenos aktuálně dostupného seznamu a detailů.
**Neznamená bezchybnou interpretaci všech cen.** `data_quality_passed` a
`quality_issues` jsou oddělené. JSON normalizace zachovává chybějící hodnoty jako
`null`, nikoliv nulu. Částky zpracovává pomocí `Decimal`, exportuje jako řetězce.
Původní odpovědi zůstávají zvlášť, bez přejmenování produktů nebo dopočítání slev.

Normalizace čte strukturované `itemsLine` i živě ověřené české `htmlPrintedReceipt`.
Novější detaily tohoto účtu při pokusu používaly HTML, starší JSON; nelze tedy
spoléhat na jeden formát nebo jednoduché pravidlo „staré = HTML“.
HTML parser zachovává opakované stejné produkty, navazuje řádek množství na produkt,
čte váhové prefixy E/M/N, tisícové ceny, táru a údaj o záruce. Řádek „Cena po slevě“
není další produkt. Množství 1 odvodí jen při shodě vytištěné řádkové a jednotkové
ceny; původ množství uvádí v `quantity_source`. Neznámé řádky hlásí.
Interní produktové ID z HTML neoznačuje za EAN. Znaménka slev nemění.
Chybějící měna/datum/povinná cena i vadné slevy se hlásí v reportu.

`reconciliation` uvádí součet `originalAmount`, absolutní součet položkových slev
a hypotézy, které přesně odpovídají celku (`original_equals_total` nebo
`original_minus_absolute_discounts`, případně s explicitním zaokrouhlením).
Hotovostní zaokrouhlení se čte z vytištěného řádku `zaokrouhleno` a exportuje
jako `rounding`; žádná obecná tolerance se na nesedící součet nepoužívá.
Strukturované doklady používají `payments[].roundingDifference` a zvláštní
`deposit.amount`. Obě korekce se zahrnou právě jednou; původ je zachován.
Jde o diagnostiku, ne důkaz významu ceny.
Znaménka původních slev jsou zachována. Zálohy, vratky, zaokrouhlení a souhrnné
slevy mohou vyžadovat další pravidla. `totalAmount` se automaticky nesnižuje
o `totalDiscount`, aby nevzniklo dvojí odečtení. Čisté ceny položek zatím
neodvozujeme. Neshoda součtů se hlásí jako `totals_unreconciled`.

Po každém exportu kontrolujte počet účtenek a stránek, rozsah dat, formáty detailů,
zastoupení slev a seznam vadných polí. Je třeba srovnat nejstarší dostupnou
účtenku a několik nákupů/slev/vratek s oficiální aplikací. Úplnost všech nákupů
nelze odvodit pouze ze shody s `totalCount`.

Offline opakovaná kontrola (žádný login ani síť):

```powershell
uv run --locked python scripts/check_export.py
# Při více profilech:
uv run --locked python scripts/check_export.py data/CZ/<profil>
```

Skript načte původní index a raw detaily, znovu zpracuje položky a slevy, zapíše
agregace do `quality.json`. Kód návratu 0 znamená shodu kontroly schématu a součtů,
2 znamená nálezy nebo chybějící detaily. Osobní obsah účtenek nevypisuje.
Kontroluje také množství × jednotkovou cenu proti řádkové částce a vytištěnou
cenu po slevě proti původní částce mínus položkové slevy. Vážené řádky
při násobení zaokrouhluje na haléře metodou HALF_UP; součty účtenek přesné.

## Testy

```powershell
uv run --locked pytest -q
uv run --locked ruff check cartalytix tests
```

Testy používají pouze syntetická data a HTTP transport bez sítě. Nepřihlašují se.
Verze závislostí a jejich kontrolní součty jsou v `uv.lock`.
Veřejné referenční knihovny nejsou instalovány ani importovány.

## Mixit.cz

Živě ověřeno 17. 10. 2026: 24 objednávek (2021-08-19 až 2026-08-25), všechny
detaily, řádky `cena × množství` i složení mixů sedí. U 21/24 objednávek se
celek liší od součtu položek: detail neobsahuje dopravu, slevy ani kredity,
rozdíl se exportuje jako `unexplained_difference` (`totals_unreconciled`).

```powershell
uv run --locked mixit login --browser chrome
uv run --locked mixit export
```

Přihlášení probíhá ručně v okně mixit.cz; okno se po zachycení tokenu samo zavře.
Skript přečte `localStorage.token`, ověří hlavičku, kterou ho web posílá na
`api.mixit.cz/api/vN/`, a uloží ho do Credential Manageru (`Cartalytix.Mixit`).
Export: `customer/orders?page=N` (úplnost podle `totalRecords`) a
`customer/orders/{hash}` do `data/mixit/CZ/<profil>/`. Obnova tokenu není
implementována; po vypršení spusťte `login` znovu. `report.json` obsahuje
`schema` (pouze cesty klíčů a typy, bez hodnot) pro návrh normalizace položek.

## Bolt Food

Živě ověřeno 18. 10. 2026: 129 objednávek (7 stránek `page_token`), všechny detaily,
obnova tokenu `getAccessTokenWithRotation` funguje. Přihlášení telefon + SMS kód
proběhlo bez captchy. Tokeny přicházejí v odpovědi `verification/confirm`
(`user_jwt_authentication`). Detail obsahuje `user_baskets`, `total_price_components`,
dopravu, poplatky, kredity a refundy.

```powershell
uv run --locked boltfood login --browser chrome
uv run --locked boltfood export
```

Přihlášení (telefon, SMS kód, případná captcha) probíhá ručně v okně food.bolt.eu;
po přihlášení otevřete Objednávky a detail jedné objednávky. Skript zachytí
refresh token (Credential Manager `Cartalytix.BoltFood`) a šablony požadavků
webu (`work/boltfood-session.json`). `work/boltfood-flow.json` obsahuje jen názvy
cest, parametrů a klíčů a příznak `challenge_seen`, tedy zda Bolt požadoval captchu.
Web i export narážejí na 429 (`code=1005`); export čeká mezi požadavky 1 s.

## Wolt

Živě ověřeno 18. 10. 2026: přihlášení magic linkem z e-mailu (web nenabízí telefon),
obnova tokenu `grant_type=refresh_token` funguje, 21 objednávek a všechny detaily.
Historie `order-tracking-api/v1/order_history/?limit=50`, detail
`order_history/purchase/{id}`. Součet položek = `items_price`; vzorec celku
(doprava, poplatky, slevy, kredity) zatím neověřen.

```powershell
uv run --locked wolt login --browser chrome
uv run --locked wolt export
uv run --locked wolt refresh-check
```

Přihlášení probíhá ručně v okně wolt.com. Po otevření historie a detailu objednávky
okno zavřete. `work/wolt-flow.json` obsahuje jen názvy cest, parametrů a klíčů a
schémata odpovědí s objednávkami bez hodnot; raw odpovědi jsou v `data/wolt/`.
Refresh token (Credential Manager `Cartalytix.Wolt`) se posílá jen na
`authentication.wolt.com`. Export se doplní podle zachyceného schématu.

## Odhlášení

`uv run cartalytix logout` odstraní pouze lokální refresh token. Neukončuje
serverovou relaci a nemaže exporty. Pro odvolání relací použijte možnosti účtu Lidl.
Detail výzkumu a hranice ověření: [docs/research.md](docs/research.md).
