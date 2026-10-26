"""Shoptet adapter and normalization; synthetic Shoptet markup and HTTP transport only."""

import hashlib
import json

import httpx
import pytest

from cartalytix.providers.shoptet.client import ShoptetClient, ShoptetError, sign_in
from cartalytix.providers.shoptet.normalization import parse_list, validate_order

SITE = "https://www.nejkafe.cz"

SETTINGS_HTML = '<form><input type="email" name="email" value="Owner@Example.com"></form>'
LIST_HTML = """
<table class="table customer-orders"><thead><tr><th>Kód</th><th>Datum</th></tr></thead><tbody>
<tr><td><span class="client-table-heading">Kód</span>
<a href="/klient/objednavky/?code=100"><strong>100</strong></a></td>
<td><span class="client-table-heading">Datum</span><span>12.7.2026 21:03</span></td>
<td>1&nbsp;189,29 Kč</td><td><strong>838 Kč</strong></td><td>Placeno</td><td></td></tr>
<tr><td><a href="/klient/objednavky/?code=../x">bad</a></td><td></td><td></td><td></td></tr>
</tbody></table>
<a href="/klient/objednavky/strana-2/">2</a>
"""


def detail_html(total: str = "838 Kč") -> str:
    return f"""
<h2>Objednávka č. 100</h2>
<table class="table table-order-details"><thead><tr><th>Název</th></tr></thead><tbody>
<tr><td class="p-name"><a class="p-name__itemLink" href="/olej/">Olej 500 ml</a>
<span class="p-name__codeLabel">Kód produktu:</span><em class="p-name__code">P1</em></td>
<td><span class="client-table-heading">Množství</span><strong>2 ks</strong></td>
<td>258,04 Kč</td><td>289 Kč</td><td>0 %</td><td>516,08 Kč</td><td><strong>578 Kč</strong></td></tr>
<tr><td class="p-name"><a class="p-name__itemLink" href="/zalivka/">Zálivka</a>
<em class="p-name__code">P2</em></td><td><strong>1 </strong></td>
<td>345,54 Kč</td><td>387 Kč</td><td>15 %</td><td>293,71 Kč</td>
<td><strong>328,95 Kč</strong></td></tr>
<tr><td class="p-name"><span class="p-name__itemName">Balíkovna</span>
<span class="p-name__shipping">Výdejní místo: private address</span></td><td></td>
<td>0 Kč</td><td>0 Kč</td><td>0 %</td><td>0 Kč</td><td><strong>0 Kč</strong></td></tr>
<tr><td class="p-name"><span class="p-name__itemName">PPL</span></td><td></td>
<td>53,72 Kč</td><td>65 Kč</td><td>0 %</td><td>53,72 Kč</td><td><strong>65 Kč</strong></td></tr>
<tr><td class="p-name"><span class="p-name__itemName">Kupon</span></td><td></td>
<td>-119,64 Kč</td><td>-134 Kč</td><td>0 %</td><td>-119,64 Kč</td>
<td><strong>-134 Kč</strong></td></tr>
<tr class="row-summary"><td colspan="8"><dl>
<dt>Cena celkem včetně DPH:</dt><dd><strong>{total}</strong></dd>
<dt>Cena bez DPH:</dt><dd>748,18 Kč</dd><dt>DPH:</dt><dd>89,77 Kč</dd></dl></td></tr>
</tbody></table>
"""


def test_list_parses_orders_and_pagination() -> None:
    orders, pages = parse_list(LIST_HTML, SITE)
    assert orders == [{"code": "100", "date": "2026-07-12T21:03:00", "total": "838"}]
    assert pages == {2: "https://www.nejkafe.cz/klient/objednavky/strana-2/"}


def test_order_maps_discounts_fees_and_rounding() -> None:
    summary = {"code": "100", "date": "2026-07-12T21:03:00"}
    result = validate_order(summary, {"id": "100", "html": detail_html()}, "100")
    assert result.get("issues") == []
    assert (result.get("total"), result.get("rounding"), result.get("currency")) == (
        "838",
        "0.05",
        "CZK",
    )
    items = result["items"]
    assert [item["name"] for item in items] == ["Olej 500 ml", "Zálivka", "PPL"]
    assert (items[0]["quantity"], items[0]["unit"], items[0]["original_amount"]) == (
        "2",
        "ks",
        "578.00",
    )
    assert items[1]["discounts"] == [{"name": "Sleva 15 %", "amount": "58.05"}]
    assert result.get("discounts") == [{"name": "Kupon", "amount": "134"}]
    assert "private address" not in json.dumps(result, ensure_ascii=False)


def test_zero_total_uses_paid_lines_and_mismatch_is_flagged() -> None:
    summary = {"date": "2026-07-12T21:03:00"}
    zero = validate_order(summary, {"id": "100", "html": detail_html("0 Kč")}, "100")
    assert (zero.get("total"), zero.get("issues")) == ("837.95", [])
    wrong = validate_order(summary, {"id": "100", "html": detail_html("900 Kč")}, "100")
    assert "totals_unreconciled" in wrong["issues"]
    missing = validate_order(summary, {"id": None, "html": "<p>nenalezena</p>"}, "100")
    assert {"items_unavailable", "receipt_identity_mismatch"} <= set(missing["issues"])


def shoptet(signed_in: list[bool], password: str = "secret") -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "www.nejkafe.cz"
        if request.url.path == "/action/Customer/Login/":
            form = dict(item.split("=") for item in request.read().decode().split("&"))
            # CSRF-enabled shops (Nejkafe) reject a POST without the page token.
            signed_in[0] = form.get("password") == password and form.get("__csrf__") == "csrf_t1"
            return httpx.Response(302, headers={"Location": "/klient/"})
        if request.url.path == "/login/":
            return httpx.Response(200, text="<script>shoptet.csrf.token = 'csrf_t1';</script>")
        if not signed_in[0]:
            return httpx.Response(302, headers={"Location": "/login/?backTo=%2Fklient%2F"})
        if request.url.path == "/klient/nastaveni/":
            return httpx.Response(200, text=SETTINGS_HTML)
        if request.url.params.get("code"):
            return httpx.Response(200, text=detail_html())
        return httpx.Response(200, text=LIST_HTML)

    return httpx.MockTransport(handle)


def test_sign_in_returns_hashed_account_and_rejects_bad_password() -> None:
    with httpx.Client(transport=shoptet([False])) as http:
        subject = sign_in("owner@example.com", "secret", site=SITE, http=http)
    assert subject == hashlib.sha256(b"owner@example.com").hexdigest()
    with httpx.Client(transport=shoptet([False])) as http, pytest.raises(ShoptetError) as error:
        sign_in("owner@example.com", "wrong", site=SITE, http=http)
    assert error.value.code == "credentials_rejected"


def test_client_signs_in_again_after_session_expiry() -> None:
    state = [False]
    credentials = json.dumps({"email": "owner@example.com", "password": "secret"})
    with httpx.Client(transport=shoptet(state)) as http:
        client = ShoptetClient(credentials, lambda _value: None, site=SITE, http=http, delay=0)
        assert client.page(1)["orders"][0]["code"] == "100"
        state[0] = False
        detail = client.detail("100")
        assert detail.get("id") == "100"
        assert client.page(3) == {"orders": [], "totalRecords": None}
    bad = json.dumps({"email": "owner@example.com", "password": "changed"})
    with httpx.Client(transport=shoptet([False])) as http, pytest.raises(ShoptetError) as error:
        ShoptetClient(bad, lambda _value: None, site=SITE, http=http, delay=0).page(1)
    assert error.value.code == "auth_required"


@pytest.mark.parametrize("csrf", [True, False])
@pytest.mark.parametrize("via_client", [True, False])
def test_rejected_login_retains_post_status_after_verify(csrf: bool, via_client: bool) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/":
            body = "<script>shoptet.csrf.token = 'private_csrf';</script>" if csrf else ""
            return httpx.Response(200, text=body)
        if request.url.path == "/action/Customer/Login/":
            return httpx.Response(403, text="private response content")
        return httpx.Response(302, headers={"Location": "/login/"})

    credentials = json.dumps({"email": "owner@example.com", "password": "private_password"})
    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as http,
        pytest.raises(ShoptetError) as error,
    ):
        if via_client:
            ShoptetClient(credentials, lambda _value: None, site=SITE, http=http).page(1)
        else:
            sign_in("owner@example.com", "private_password", site=SITE, http=http)
    assert error.value.code == ("auth_required" if via_client else "credentials_rejected")
    assert error.value.diagnostic == {"phase": "login", "status": 403, "csrf_present": csrf}


def test_login_verify_redirect_reports_session_not_authenticated() -> None:
    with httpx.Client(transport=shoptet([False])) as http, pytest.raises(ShoptetError) as error:
        sign_in("owner@example.com", "wrong", site=SITE, http=http)
    assert error.value.diagnostic == {
        "phase": "login_verify",
        "status": 302,
        "reason": "session_not_authenticated",
        "csrf_present": True,
    }


@pytest.mark.parametrize(
    ("path", "phase"),
    [
        ("/login/", "login_page"),
        ("/action/Customer/Login/", "login"),
        ("/klient/nastaveni/", "login_verify"),
    ],
)
def test_login_transport_failure_reports_active_phase(path: str, phase: str) -> None:
    upstream = shoptet([False])

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == path:
            raise httpx.ConnectError("private password csrf response", request=request)
        return upstream.handle_request(request)

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as http,
        pytest.raises(ShoptetError) as error,
    ):
        sign_in("owner@example.com", "secret", site=SITE, http=http)
    assert error.value.code == "provider_unavailable"
    assert error.value.diagnostic.get("phase") == phase
    assert error.value.diagnostic.get("transport") == "ConnectError"
    assert "private" not in json.dumps(error.value.diagnostic)


@pytest.mark.parametrize("detail", [True, False])
@pytest.mark.parametrize("status", [403, 302])
def test_order_failure_reports_history_or_detail_phase(detail: bool, status: int) -> None:
    upstream = shoptet([False])

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/klient/objednavky/":
            return httpx.Response(status, headers={"Location": "/login/"})
        return upstream.handle_request(request)

    credentials = json.dumps({"email": "owner@example.com", "password": "secret"})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = ShoptetClient(credentials, lambda _value: None, site=SITE, http=http, delay=0)
        with pytest.raises(ShoptetError) as error:
            client.detail("100") if detail else client.page(1)
    assert error.value.code == ("auth_required" if status == 302 else "provider_unavailable")
    assert error.value.diagnostic.get("phase") == ("detail" if detail else "history")
    assert error.value.diagnostic.get("status") == status
