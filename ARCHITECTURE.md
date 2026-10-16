# Cartalytix — pracovní návrh architektury

Stav: návrh k diskusi, 16. 10. 2026. Technologické cíle a otevřené volby níže;
současný ověřený CLI experiment zatím běží samostatně na Pythonu 3.12.

## Cíl a rozsah

Self-hosted aplikace dostupná z internetu, vázaná na uživatelský účet.
Automaticky sbírá běžné nákupy, normalizuje, filtruje a agreguje data do dashboardů.
V aktuálním rozsahu jeden účet Cartalytix vlastní data a může připojit více účtů
stejného poskytovatele: např. tři Lidl účty tří různých lidí. Každé připojení má
vlastní tokeny, název, historii a stav synchronizace; přehled lze zobrazit
společně nebo filtrovat podle připojeného účtu. Sdílené domácnosti a role neřešíme.
Kategorizace včetně AI a vlastních AI klíčů je odložená.

Poskytovatelé: Lidl, Albert, Globus, Rohlík, Košík, Billa. Získávání dat se ověří
pro každého zvlášť; nyní je živě potvrzen Lidl CZ. Schopnosti adaptérů mohou být
odlišné: účtenky/objednávky, položky, slevy, historie, aktualizace a způsob přihlášení.

## Technologie

| Oblast     | Volba / návrh                                                                                                                        |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| Backend    | FastAPI, Python **3.15** (interpretace „py15“), SQLAlchemy 2.x, Alembic, uv                                                   |
| Kvalita    | Ruff (lint + formát), ty (type checking), pytest, pre-commit; stejné kontroly v CI                                                  |
| CI         | **GitHub Actions**: testy backendu a frontendu na push/PR, každá část pouze při relevantní změně                        |
| Překlady  | Anglické zdrojové texty, česká lokalizace `cs`; backend gettext, frontend Vue I18n                                              |
| Frontend   | **Vue 3 + TypeScript + Vite**, Vue Router, TanStack Query pro serverová data; **mobile-first PWA** přes vite-plugin-pwa |
| Databáze  | PostgreSQL v Dockeru, perzistentní volume                                                                                            |
| Úlohy     | Doporučené **Celery worker + Celery Beat**, Redis broker, stav běhů v PostgreSQL                                            |
| Cache      | Cache agregací volitelná až podle měření; **Redis**. Dragonfly pouze při prokázaném přínosu                          |
| Deployment | Docker Compose: API, frontend, Celery worker, jeden Beat, PostgreSQL, Redis broker; HTTPS a směrování přes existující Nginx na VPS |

Python 3.15 je vydaný; kompatibilitu všech závislostí, ovladače PostgreSQL,
Playwrightu, Celery a vývojových nástrojů ověří implementační spike a CI před migrací.

## CI — GitHub Actions podle změněných částí

GitHub Actions běží při pushi commitů a na pull requestech. Nejprve zjistí změněné části,
potom spustí pouze dotčené kontroly; lokální pre-commit zůstává doplňkem CI.

| Změna | Spuštěné kontroly |
| -------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Backend, jeho testy, migrace, Python konfigurace/lockfile nebo backend CI | Backend: Ruff, ty, pytest; testovací PostgreSQL/Redis podle potřeby |
| Frontend, jeho testy, překlady, JS/TS konfigurace/lockfile nebo frontend CI | Frontend: lint, kontrola TypeScript/Vue, jednotkové/komponentové testy a produkční PWA build |
| Sdílený API kontrakt, společná infrastruktura nebo společná CI konfigurace | Obě části a dotčené integrační kontroly; změny API ověřit i proti frontend klientu |
| Pouze dokumentace nebo experimenty | Bez testů hlavní aplikace |

Změna frontendového katalogu nevyvolá backend testy; změna backendového katalogu
nevyvolá frontend testy. Detekce zahrnuje i závislosti, přejmenování a smazání
souborů; při nejistém diffu spustí obě části. Nezměněný frontend se netestuje
jen proto, že se změnil nesouvisející backendový soubor.

Detektor změn a závěrečný souhrnný check běží vždy. Ten vyžaduje úspěch všech
relevantních kontrol a přijímá záměrně přeskočené ostatní joby; selhání detektoru
není úspěch. Tím required check neuvízne při přeskočení celého workflow podle cest.

## Uspořádání a tok dat

Modulární backend v jednom projektu; FastAPI a Celery worker sdílejí doménový
kód i obraz. Webový požadavek zadá synchronizaci a vrátí ID běhu, nečeká na stažení.
Beat pouze plánuje, worker provádí import; běží právě jedna instance Beat.
Doporučený plán: Beat periodicky zadá dispatcher, který podle nastavení v DB
vybere připojení k synchronizaci. Ruční spuštění zadá stejnou úlohu pro konkrétní
připojení. Intervaly účtů a příští termíny jsou v PostgreSQL.

Stav, průběh a chyby patří do `SyncRun` v DB; Celery result backend není nutný.
Předání DB → broker musí být obnovitelné (outbox / opětovné odeslání čekajících
úloh). Počítat s opakovaným doručením, idempotencí, timeouty, omezeným retry
a zámkem připojení. Redis broker má perzistenci, volume a politiku `noeviction`;
cache s evikcí případně dostane samostatnou instanci. Samotné různé Redis DB
neoddělují paměť ani eviction. Worker a Beat poběží v Dockeru i při vývoji.

```text
Beat / ruční spuštění → Redis broker → Celery worker
                                       ↓
Poskytovatel → raw data → normalizace + kontrola → PostgreSQL
                                                     ↓
                                      SQL agregace → API → Vue PWA
```

Každý poskytovatel má vlastní adaptér a FastAPI router pod
`/api/v1/providers/{provider}/…`: propojení účtu, reautorizace, synchronizace.
Sdílený kontrakt adaptérů sjednocuje průchod historií, stažení detailu a stav chyb.
Normalizované účtenky, produkty a analytika mají společné endpointy
(`/receipts`, `/products`, `/analytics`), aby frontend nemusel
pro každý obchod implementovat odlišný dashboard. Seznamy mají filtry a stránkování.

## Data a synchronizace

Základní entity: `User`, `ProviderConnection`, `SyncRun`, `RawDocument`,
`Receipt`, `ReceiptItem`, `Discount`, `Product`, `ProductAlias`.
Značka řetězce, konkrétní prodejna a připojený účet jsou
odlišné pojmy. Produktový alias reprezentuje identitu/název u poskytovatele;
globální párování produktů mezi obchody bude postupné a opravitelné.

- Všechna soukromá data mají vlastníka; autorizace platí i pro joby, exporty,
  agregace a cache. `ProviderConnection` nese poskytovatele, zemi, přezdívku účtu
  a stav autorizace. Uživatel může mít několik stejných poskytovatelů.
- Raw odpověď se uloží s kontrolním součtem a verzí parseru; normalizace je
  opakovatelná. Původní payload v PostgreSQL text/BYTEA, vybraná metadata v JSONB
  (JSONB samo nezachovává původní zápis odpovědi).
- Idempotence přes `(connection_id, external_receipt_id)` a původní pořadí položek.
  Opakované stejné produkty nejsou duplicity. Duplicitní nákup mezi dvěma účty
  se neslučuje pouze podle data a částky.
- Množství a peníze: `Decimal` / PostgreSQL `NUMERIC`, měna, jednotky a velikost
  balení. Zachovat řádkové i jednotkové ceny, slevy, zálohy, zaokrouhlení, vratky
  a původ hodnot; čistou cenu nikdy neodhadovat bez ověřených pravidel poskytovatele.
- Periodický i ruční import používají stejnou cestu, transakce po dokladech,
  zámek pro dané připojení, omezené retry/backoff a obnovení po chybě. Síťové
  čekání nedrží otevřenou transakci. Obnova rotujícího tokenu je serializovaná.
- Využít skutečné možnosti zdroje: cursor/delta, pokud existuje; jinak seznam
  - známá ID + periodická obnova detailů kvůli opravám a vratkám. U Lidlu nyní
    celý seznam, stažení chybějících detailů a občasné obnovení známých.
- Viditelný poslední úspěch, průběh, počet dokladů, úplnost a chyby kvality.
  Nedostupný obchod nesmí zastavit ostatní. Zmizelý doklad se automaticky nemaže.

## Agregace, dashboard a cache

Agregace počítá PostgreSQL nad normalizovanými daty: výdaje podle období,
řetězce/prodejny/připojeného účtu, četnost nákupů, průměrný nákup, položkové i celkové
slevy, nejčastější produkty a historie srovnatelných jednotkových cen.
Vratky a zálohy rozlišit od spotřeby; různé měny nesčítat bez výslovného přepočtu.

Začít vhodnými indexy a SQL agregacemi. Opakovaně drahé přehledy lze uložit
do souhrnných tabulek nebo materializovaných pohledů; jejich obnova je explicitní,
standardní `REFRESH MATERIALIZED VIEW` není automatická inkrementální aktualizace.
Postupné přepočty dotčených období jsou vhodné pro souhrnné tabulky, až je potřeba.

Volitelná Redis cache ukládá výsledky API agregací: klíč obsahuje vlastníka,
filtry a verzi dat. Verze se mění až po commitu importu či opravy; TTL je pojistka.
Frontend zobrazuje čerstvost dat. Vyprázdnění cache nesmí ztratit jediný nákup.
Dragonfly nabízí zejména jiné škálování a práci s pamětí; pro očekávané zatížení
nemáme benchmark ani důvod jej nyní preferovat před Redisem.

## Frontend a PWA

Vue 3 je zvolený framework; pro tento dashboard nevidíme důvod měnit uživatelovu
preferenci na React. Mobile-first rozvržení, dotykové ovládání, pohodlné filtrování
a responzivní grafy/tabulky. FastAPI OpenAPI bude zdroj typů API klienta.

Instalace z podporovaného prohlížeče: HTTPS, web app manifest, ikony, `start_url`,
scope a `display: standalone`. Service worker přes `vite-plugin-pwa` cachuje
statický app shell a nabízí řízenou aktualizaci. Instalační postup se liší podle
platformy; ověřit Android Chrome, iOS Safari a desktop.

Soukromé API odpovědi, přihlašovací endpointy a tokeny service worker standardně
necachuje. Při výpadku zobrazit offline stav; případné offline nákupní údaje jsou
samostatná budoucí volba s mazáním při odhlášení. Periodické synchronizace běží
na serveru přes Celery i při zavřené PWA, nezávisle na omezeních mobilního pozadí.

## Jazyk a překlady

Zdrojový jazyk aplikace je **angličtina**: identifikátory, logy a výchozí
uživatelské texty. Všechny uživatelské texty od začátku přes překladovou funkci;
čeština jako lokalizace **`cs`**, formátování např. `cs-CZ`. Výchozí jazyk a
fallback `en`; uživatel může přepnout na češtinu a volba se uloží.

- **Backend:** gettext, `_()` pro texty a `ngettext()` pro plurály; anglické
  `msgid`, české `.po` katalogy, kompilované `.mo`. Překládat v kontextu konkrétního
  požadavku/úlohy, neměnit globální jazyk běžícího procesu. Proměnné dosazovat
  přes pojmenované parametry až po překladu, neskládat věty z fragmentů.
- **Frontend:** doporučení Vue I18n, `t()` / `$t()` a katalogy `en` + `cs`.
  Anglické znění v katalogu `en`, stabilní klíče v komponentách. Backendové a
  frontendové katalogy mají stejná jazyková pravidla, vlastní nástroje/formát.
- API chyby a stavové zprávy mají stabilní kód + parametry; frontend překládá
  jejich zobrazení. Překlad není součást identifikátoru chyby ani DB stavu.
- Lokalizovat také validace, prázdné stavy, grafy, přístupné popisky a PWA hlášky;
  datum, čísla a měnu formátovat podle locale. Názvy produktů a raw data z obchodů
  zachovat v původním znění. Locale nemění měnu ani uloženou finanční hodnotu.
- CI příslušné části kontroluje katalogy, chybějící překlady/klíče, parametry
  a české plurály; frontend testy pokryjí přepnutí jazyka a anglický fallback.

## Provoz, přihlášení a kontrola N+1

- Vlastní účet aplikace oddělit od účtů obchodů. Zvoleno: lokální účet s heslem,
  uzavřená registrace a založení správcem přes lokální CLI.
  Browser session přes HttpOnly/Secure/SameSite cookies, ochrana CSRF a rate limit loginu.
- Samostatný proxy kontejner nepřidávat. Existující Nginx na VPS ukončuje HTTPS
  a směruje `/api/` na API, ostatní cesty na statický frontend pod stejným originem.
  Porty API a frontendu publikovat pouze na loopbacku; DB a Redis nezveřejňovat.
- Tokeny poskytovatelů šifrovat aplikačně v DB; hlavní klíč mimo DB
  přes Docker secret. HTTPS, redakce logů, oddělená interní síť DB/cache.
- Vyřešit interaktivní propojení účtu v Dockeru: současný lokální Chrome a
  WinVault nejsou produkční řešení. Preferovat podporovaný OAuth; kde chybí,
  navrhnout zabezpečený asistovaný login. MFA/CAPTCHA může vyžadovat zásah uživatele.
- Zálohovat DB, raw payloady i obnovitelnou konfiguraci klíčů; ověřit obnovu.
  Healthchecky, logy úloh a měření pomalých SQL patří k základnímu provozu.
- N+1: explicitní eager loading, SQLAlchemy `raiseload` / `lazy="raise_on_sql"`,
  limity počtu SQL dotazů v endpointových testech a SQL profilování ve vývoji.
  `raiseload` nezachytí ručně psaný SELECT v cyklu. Kandidát navíc:
  `nplus1[sqlalchemy]`; ověřit Python 3.15 a async integraci, nespoléhat jen na něj.

## Otevřené volby a pořadí

1. Bezpečný způsob interaktivního propojení Lidl účtů v Dockeru.
2. Výchozí interval synchronizací, rozsah pravidelné obnovy starších detailů
   a praktické limity poskytovatelů; nastavení po připojeních v DB.

Postup: rozhodnout otevřené základy → ověřit Python 3.15 a login v Dockeru →
DB + účet + jeden Lidl konektor + Celery/Beat → společné API a mobile-first Vue PWA →
průzkum dalších obchodů → pokročilé přehledy a případná cache.
Kategorizace a AI zůstávají odložené mimo tuto etapu.

## Ověřené technické zdroje

- [Python 3.15](https://www.python.org/downloads/release/python-3150/)
- [SQLAlchemy: raiseload](https://docs.sqlalchemy.org/en/20/orm/queryguide/relationships.html#preventing-unwanted-lazy-loads-using-raiseload)
- [nplus1](https://github.com/huynguyengl99/nplus1)
- [Vue + TypeScript](https://vuejs.org/guide/typescript/overview.html), [Vite PWA + Vue](https://vite-pwa-org.netlify.app/frameworks/vue.html), [instalace PWA](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable)
- [Celery Beat](https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html), [Celery + Redis](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html)
- [PostgreSQL: REFRESH MATERIALIZED VIEW](https://www.postgresql.org/docs/18/sql-refreshmaterializedview.html)
- [Dragonfly: architektura a kompatibilita](https://github.com/dragonflydb/dragonfly)
- [CI: filtrování změn a required checks](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)
- [Python gettext](https://docs.python.org/3/library/gettext.html), [Vue I18n](https://vue-i18n.intlify.dev/guide/essentials/started), [fallback](https://vue-i18n.intlify.dev/guide/essentials/fallback)
