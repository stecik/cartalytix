"""Synthetic Shoptet fixtures reproducing observed Fermato markup, no real orders."""

from pathlib import Path

from cartalytix.fermato import ORDERS, account_profile, collect, normalize, parse_list

LIST = """
<table class="table customer-orders"><thead><tr><th>Kód</th><th>Datum</th></tr></thead><tbody>
<tr><td><span class="client-table-heading">Kód</span>
<a href="/klient/objednavky/?code=100"><strong>100</strong></a></td>
<td><span class="client-table-heading">Datum</span><span>12.7.2026 21:03</span></td>
<td class="cell--price"><span class="client-table-heading">Cena bez DPH</span> 1&nbsp;189,29 Kč</td>
<td class="cell--price"><span class="client-table-heading">Cena celkem</span><strong>1&nbsp;332 Kč</strong></td>
<td><span class="client-table-heading">Stav platby</span> Placeno </td><td>Není k dispozici</td></tr>
<tr><td><a href="/klient/objednavky/?code=../x">bad</a></td><td></td><td></td><td></td></tr>
</tbody></table>
<a href="/klient/objednavky/strana-2/">2</a>
"""

DETAIL = """
<table class="table table-order-details"><thead><tr><th>Název</th></tr></thead><tbody>
<tr><td class="p-name"><a class="p-name__itemLink" href="/olej/">Olej 500 ml</a>
<span class="p-name__codeLabel">Kód produktu:</span><em class="p-name__code">P1</em></td>
<td><span class="client-table-heading">Množství</span><strong>2 ks</strong></td>
<td>258,04 Kč</td><td>289 Kč</td><td>0 %</td><td>516,08 Kč</td><td><strong>578 Kč</strong></td></tr>
<tr><td class="p-name"><a class="p-name__itemLink" href="/zalivka/">Zálivka</a>
<em class="p-name__code">P2</em></td>
<td><strong>1 </strong></td>
<td>345,54 Kč</td><td>387 Kč</td><td>15 %</td><td>293,71 Kč</td><td><strong>328,95 Kč</strong></td></tr>
<tr><td class="p-name"><span class="p-name__itemName">Balíkovna</span>
<span class="p-name__shipping">Výdejní místo: 1</span></td><td></td>
<td>0 Kč</td><td>0 Kč</td><td>0 %</td><td>0 Kč</td><td><strong>0 Kč</strong></td></tr>
<tr><td class="p-name"><span class="p-name__itemName">Kupon</span></td><td></td>
<td>-61,61 Kč</td><td>-69 Kč</td><td>0 %</td><td>-61,61 Kč</td><td><strong>-69 Kč</strong></td></tr>
<tr class="row-summary"><td colspan="8"><dl>
<dt>Cena celkem včetně DPH:</dt><dd><strong>837,95 Kč</strong></dd>
<dt>Cena bez DPH:</dt><dd>748,18 Kč</dd><dt>DPH:</dt><dd>89,77 Kč</dd></dl></td></tr>
</tbody></table>
"""


def test_list_parses_orders_and_pagination():
    orders, pages = parse_list(LIST)
    assert orders == [
        {
            "code": "100",
            "date": "2026-07-12T21:03:00",
            "total_net": "1189.29",
            "total": "1332",
            "payment_state": "Placeno",
        }
    ]
    assert pages == ["https://www.fermato.cz/klient/objednavky/strana-2/"]


def test_detail_items_adjustments_and_totals():
    result = normalize(
        "100", DETAIL, {"date": "2026-07-12T21:03:00", "total": "837.95"}
    )
    assert result["currency"] == "CZK"
    assert result["total"] == "837.95"
    assert result["vat"] == "89.77"
    assert [item["code"] for item in result["items"]] == ["P1", "P2"]
    assert result["items"][0]["quantity"] == "2"
    assert result["items"][0]["unit"] == "ks"
    assert result["items"][1]["unit"] is None
    assert result["items"][1]["discount_percent"] == "15"
    assert result["items"][0]["url"] == "https://www.fermato.cz/olej/"
    assert [row["name"] for row in result["adjustments"]] == ["Balíkovna", "Kupon"]
    assert result["adjustments"][0]["detail"] == "Výdejní místo: 1"
    assert result["issues"] == []


def test_detail_flags_unreconciled_and_missing_items():
    broken = DETAIL.replace("837,95 Kč", "900 Kč")
    assert "totals_unreconciled" in normalize("100", broken)["issues"]
    zero = normalize(
        "100",
        DETAIL.replace("837,95 Kč", "0 Kč")
        .replace("748,18 Kč", "0 Kč")
        .replace("89,77 Kč", "0 Kč"),
        {"total": "0"},
    )
    assert zero["issues"] == ["source_total_zero"]
    assert zero["total_source"] == "lines_sum"
    assert (zero["total"], zero["total_net"], zero["vat"]) == (
        "837.95",
        "748.18",
        "89.77",
    )
    rounded = normalize("100", DETAIL.replace("837,95 Kč", "838 Kč"))
    assert rounded["issues"] == []
    assert rounded["rounding"] == "0.05"
    assert "items_unavailable" in normalize("100", "<p>nenalezena</p>")["issues"]


def test_profile_hashes_email():
    html = '<input type="email" name="email" value=" A@Example.cz ">'
    assert account_profile(html) == account_profile(
        '<input name="email" value="a@example.cz">'
    )


def test_collect_reuses_cached_details(tmp_path: Path):
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        if url == ORDERS:
            return LIST.replace("1&nbsp;332 Kč", "837,95 Kč")
        if "strana-2" in url:
            return "<p>empty</p>"
        return DETAIL

    report = collect(fetch, tmp_path)
    assert report["complete"] is True
    assert report["unique_orders"] == 1
    assert report["downloaded_details"] == 1
    assert (tmp_path / "orders.json").exists()
    calls.clear()
    report = collect(fetch, tmp_path)
    assert report["cached_details"] == 1
    assert not any("code=" in url for url in calls)
