# Výsledek experimentu: Lidl Plus CZ

Ověřeno 16. 10. 2026 na uživatelově vlastním českém účtu.

| Kontrola | Výsledek |
| --- | ---: |
| Účtenky vrácené seznamem API | 861 |
| Stránek | 35: 34 × 25 + 11 |
| Úspěšně uložené detaily | 861 |
| Nejstarší dostupná účtenka | 25. 10. 2021 |
| Nejnovější dostupná účtenka | 14. 10. 2026 |
| Detaily HTML / strukturovaný JSON | 849 / 12 |
| Produktové řádky | 4 836 |
| Řádky položkových slev | 596 |
| Vážené položky | 385 |
| Shodné součty účtenek | 861 / 861 |
| Shodné množství × jednotková cena | 4 836 / 4 836 |
| Shodné vytištěné ceny po slevě | 552 / 552 |
| Chybějící či vadné detaily | 0 |
| Nálezy posledního offline auditu | 0 |

Částky jsou zpracovány přes `Decimal`. Součty přesně odpovídají zdrojovým
celkům po zohlednění položkových slev, explicitních vratných záloh a skutečného
hotovostního zaokrouhlení. Násobení vážených položek používá HALF_UP na haléře.
Tato shoda nepotvrzuje úplnost všech celoživotních nákupů ani fiskální správnost.

## Přihlášení a soukromí

Ruční přihlášení v novém lokálním Chromu fungovalo. Heslo a ověřovací kód šly
přímo do formuláře Lidl. Refresh token je ve Windows Credential Manageru;
access token pouze v paměti. Žádné tokeny, hesla ani cookies v projektových
JSON, příkazových argumentech nebo chatu. Exporty obsahují soukromé nákupní
údaje a zůstávají v ignorovaném adresáři `data/`.

## Výsledky testu synchronizace

První úplný sběr využil 9 detailů z řízeného průřezového vzorku a stáhl dalších
852. Následující úplný průchod znovu ověřil seznam všech 35 stránek a použil
861 známých detailů z cache; stáhl 0 nových detailů. Stejné ID účtenky proto
umožňuje budoucí inkrementální stahování detailů bez opakovaného přihlášení.

Současné řešení pro jistotu vždy prochází celý seznam. Neověřili jsme veřejně
podporovaný delta endpoint, serverový snapshot ani garanci stabilního řazení.
Pravidelný `--refresh-details` je nutný pro změny již známých účtenek/vratky.
Bez nového skutečného nákupu nelze živě předvést příchod nového ID; opožděná
starší účtenka a obnovení změněného detailu jsou pokryty syntetickými testy.

## Důležité zjištěné rozdíly API

- Seznam v2 fungoval s `cs-CZ`; detail v3 s touto hlavičkou vracel HTTP 400.
  Stejný detail s `cs` uspěl. Opraveno pouze pro detail.
- Novější CZ účtenky při tomto sběru používaly HTML, nejstarší strukturovaný
  JSON. Je nutná podpora obou formátů.
- HTML může mít produkt bez atributů, s navazujícím váhovým řádkem, tárou,
  jednotkovou cenou typu `2 490, Kč/kg`, zárukou a samostatným řádkem ceny po slevě.
- Strukturované detaily nesou zálohu v `itemsLine[].deposit.amount` a
  zaokrouhlení v `payments[].roundingDifference`. Neodvozujeme korekci z rozdílu
  celku. Údaje o vrácené hotovosti nepřičítáme k nákupu.

Rozsah 2021–2026 je výsledek konkrétního účtu. Není to obecná retenční lhůta
Lidl ani důkaz, že česká historie začíná právě v roce 2021. Srovnání se všemi
účtenkami v oficiální aplikaci, zahraniční nákupy a případné vratkové doklady
zůstávají samostatnou kontrolou; do exportu nevymýšlíme chybějící data.

## Použití

```powershell
uv run --locked cartalytix export
uv run --locked python scripts/check_export.py
```

Finální `receipts.json`, raw odpovědi, `index.json`, `report.json` a `quality.json`
jsou pod `data/CZ/<lokální-profil>/`. Podrobnosti příkazů a bezpečnosti jsou
v [README](../README.md); porovnání knihoven a zdroje v [průzkumu](research.md).

Regresní sada: 43 testů; kontrola stylu prošla. Nezávislé statické review našlo
dva okrajové případy (název balení a kombinace JSON+HTML metadat); oba opraveny,
pokryty regresními testy. Nic necommitováno, dashboard ani finální architektura
nevytvořeny.
