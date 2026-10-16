# Lidl Plus CZ: průzkum 2026-10-16

## Kandidáti a volba experimentu

| Projekt | Ověřeno ve veřejných zdrojích | Omezení pro tento experiment |
| --- | --- | --- |
| [Andre0512/lidl-plus](https://github.com/Andre0512/lidl-plus), [PyPI](https://pypi.org/project/lidl-plus/) | PyPI 0.3.5; Selenium/Selenium Wire, OAuth PKCE; seznam i detail ve v2; hlavička `999.99.9` | Starý auth stack a endpoint detailu. Neukládat token přes CLI výpis. |
| [zsobix/lidlplus-api](https://github.com/zsobix/lidlplus-api), [PyPI](https://pypi.org/project/lidlplus-api/) | PyPI 0.0.5 z 2026-04-20, repo poslední push 2026-08-11; Playwright, seznam v2, detail v3; GPLv3 | Auth a další metody nad rámec read-only exportu. README a metadata mají odlišný požadavek na Python. |
| [RoryDotGG/iLidl](https://github.com/RoryDotGG/iLidl), [PyPI](https://pypi.org/project/ilidl/) | Python >=3.12, MIT; poslední push 2026-03-19; Playwright; realistická App-Version 16.45.5; v2/v3 | Normalizuje hlavně britské HTML. Token v TOML, debug může zobrazit autorizační kód. Rotovaný token není v klientu automaticky trvale uložen. |
| [klajbard/lidl-plus-go](https://github.com/klajbard/lidl-plus-go) | Poslední push 2026-08-04; dokumentuje potíže hlavičky `999.99.9` | Užitečné porovnání protokolu, není Python. |
| [KamKubicki/fidl-plus](https://github.com/KamKubicki/fidl-plus) | Ruční browser PKCE, přesměrování custom scheme, strukturované i HTML účtenky | Celá aplikace nad rámec zadání; tokeny v JSON; bez ověření českého HTML. |
| [sr-patel/lidl-shopping-analyzer](https://github.com/sr-patel/lidl-shopping-analyzer) | Webové cookies, primárně UK, inkrementální export | Neověřená přenositelnost UK webové session do CZ. Nečíst běžný profil prohlížeče. |

Čas posledního push je údaj GitHub API v okamžiku průzkumu, ne důkaz funkčnosti.
PyPI verze byly ověřeny přes veřejné stránky; zdrojový kód výše uvedených hlavních
klientů byl porovnán. Neprovedl se audit jejich všech závislostí ani živý login
každé knihovny. Referenční zdroje ve `work/` nejsou součástí distribuovaného balíku.

Zvolen malý vlastní adaptér HTTP + Playwright + explicitní WinVault backend.
Důvod: neztratit raw data, nemíchat GB parser s CZ daty, neukládat tokeny do textu,
nepřijímat automaticky nové podmínky a omezit přístup na účtenky.
Implementace používá veřejně pozorovaný protokol, nekopíruje celé moduly klientů.

## Přihlášení

`accounts.lidl.com/connect/authorize`, veřejný nativní klient
`LidlPlusNativeClient`, PKCE S256, callback `com.lidlplus.app://callback`.
Po ručním ověření následuje token exchange na `/connect/token`.
Hodnota `secret` v tomto veřejném klientském protokolu není uživatelovo heslo.
Server může změnit scopes, formulář, klienta, CAPTCHA i požadované hlavičky.
Vlastní stavba autorizačního URL neznamená garantovanou podporu třetích stran.

Žádné heslo neprochází Pythonem. Callback se zachytí v dočasném lokálním
prohlížeči; ověří se `state` a teprve pak se vymění kód. Tokeny ani návratové URL
se nevypisují. Při chybě se nezveřejňuje response body ani traceback třetí strany.
Nedáváme pevnou garanci životnosti refresh tokenu: záleží na serveru a rotaci.

## Historie a region

[České oznámení spuštění Lidl Plus](https://spolecnost.lidl.cz/pro-novinare/tiskove-zpravy/lidl-predstavuje-unikatni-program-lidl-plus)
z roku 2020 uvádělo trvalé uchování účtenek v historii nákupů. To není záruka
dnešní retence neoficiálního endpointu. Britský projekt uvádí hranici přibližně
únor 2023; nelze ji přenést na český účet.

[České podmínky služby](https://www.lidl.cz/c/podminky-ucasti-lidl-plus/s10007920)
popisují mimo jiné dvoufaktorové ověření. Nejde o veřejnou specifikaci API.

`CZ` + `cs-CZ` používáme explicitně. Export zatím netvrdí, že zahrnuje účtenky
zahraničních nákupů, e-shopu nebo nákupy bez identifikace karty.
Nejstarší datum v reportu je rozsah vrácených dat daného účtu, nikoliv
obecný limit Lidl. Limit retence a úplnost je nutné srovnat s oficiální aplikací.

## Úroveň důkazů

- Protokol, knihovny, zdrojové formáty: veřejný kód + aktuální zdroje.
- Bezpečný lokální login: živě úspěšný přes Chrome, token uložen ve WinVault.
- Stránkování, detekce neúplnosti, cache, rotace a numerická data: syntetické testy.
- Český seznam živě vrací 861 účtenek / 35 stránek / 25. 10. 2021 až 14. 10. 2026.
  Úplnost detailů a výsledky auditu se potvrzují až po dokončeném exportu.

## Živě zjištěné rozdíly proti obecným klientům

Stejný CZ detail se stejným tokenem: `Accept-Language: cs-CZ` → HTTP 400,
`Accept-Language: cs` → HTTP 200. Opraveno pouze pro endpoint detailu;
seznam nadále používá cs-CZ. Výstup diagnostiky neobsahoval tokeny ani body chyb.

Nové CZ detaily v průřezu účtem obsahují HTML, nejstarší detail z roku 2021
strukturované položky. HTML má i produkty bez `class=article` a bez produktových
atributů: název/cena jsou na prvním řádku, váha a cena/kg na dalším.
Ověřeny prefixy E/M/N, tára `PT:`, jednotková cena `2 490, Kč/kg`, kupóny/slevy,
řádek „Cena po slevě“, opakované produkty a metadata záruky.
Hotovostní nákupy obsahují skutečné zaokrouhlení, např. 184,52 + 0,48 = 185,00;
součet se ověřuje včetně této explicitní korekce. Číslo na konci názvu produktu
se nesmí stát tisícovou skupinou ceny: parser proto používá atribut názvu a
původní šířkové oddělení sloupců, ne pouze slova spojená jednou mezerou.
Tyto vlastnosti vyžadují vlastní CZ parser a regresní testy; prosté kopírování
GB/PL parseru by tiše ztratilo položky nebo množství.

Žádná veřejně dostupná garance stabilního delta API nebo serverového snapshotu
se v prověřených zdrojích nepotvrdila. App-Version je konfigurovatelná;
16.45.5 je hodnota z referenčního klienta, nikoliv ověřená současná verze aplikace.
