"""Albert transport tests with synthetic credentials and receipts."""

import base64
import hmac
import json
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from cartalytix.providers.albert import client as albert
from cartalytix.providers.common import ProviderPayload

PHONE = "+420000000000"
CREDENTIAL = '{"refresh_token":"synthetic-refresh","subject":"sample-account"}'
CONFIG = albert.AlbertConfig(
    username=SecretStr("synthetic-user"),
    password=SecretStr("synthetic-password"),
    verification_salt=SecretStr("synthetic-verification-salt"),
    login_salt=SecretStr("synthetic-login-salt"),
)


def tokens(
    refresh: str = "synthetic-rotated", subject: str | None = "sample-account"
) -> httpx.Response:
    claims = {"version": "2", "phone": PHONE}
    if subject is not None:
        claims["user_name"] = subject
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return httpx.Response(200, json={"accessToken": f"e30.{payload}.c2ln", "refreshToken": refresh})


def listing(receipt_ids: list[str], *, page: int = 1, total: int | None = None) -> httpx.Response:
    count = len(receipt_ids) if total is None else total
    return httpx.Response(
        200,
        json={
            "receipts": [{"receiptNumber": receipt_id} for receipt_id in receipt_ids],
            "page": {
                "number": page,
                "size": 20,
                "total": (count + 19) // 20,
                "totalElements": count,
            },
            "digitalReceipt": True,
        },
    )


def test_sms_login_signs_exact_body_using_stable_installation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(albert.time, "time", lambda: 1_700_000_000.0)
    responses = iter(
        [
            (
                "POST",
                "/auth/start",
                b'{"phoneNumber":"000000000","phonePrefix":"+420"}',
                b"synthetic-login-salt",
                httpx.Response(200, json={"startId": "synthetic-start"}),
            ),
            (
                "PUT",
                "/auth/request-code",
                b'{"phoneNumber":"000000000","phonePrefix":"+420","startId":"synthetic-start"}',
                b"synthetic-verification-salt",
                httpx.Response(
                    200, json={"loginId": "synthetic-login", "expiresAt": 1_700_000_200_000}
                ),
            ),
            (
                "POST",
                "/auth/login",
                b'{"loginId":"synthetic-login","code":"123456"}',
                b"synthetic-login-salt",
                tokens("synthetic-grant", "signed-principal"),
            ),
        ]
    )
    installations: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            assert request.url.path == "/"
            return httpx.Response(404, headers={"Date": "Tue, 14 Nov 2023 22:13:40 GMT"})
        method, path, body, salt, response = next(responses)
        assert request.url.host == "aczloyalty-auth-service-v2-prod.delhaize.eu"
        assert (request.method, request.url.path, request.content) == (method, path, body)
        assert request.headers.get("Authorization") == (
            "Basic " + base64.b64encode(b"synthetic-user:synthetic-password").decode()
        )
        assert request.headers.get("Phone-Number") == "MDAwMDAwMDAw"
        assert request.headers.get("Timestamp") == "1700000020000"
        signature = hmac.digest(salt, body + b"1700000020000", "sha256").hex()
        assert request.headers.get("Verification-Token") == signature
        installations.append(request.headers.get("Installation-Id", ""))
        return response

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        flow = albert.start_verification(PHONE, config=CONFIG, http=http)
        assert (flow.phone, flow.login_id, flow.expires_at) == (
            PHONE,
            "synthetic-login",
            1_700_000_180,
        )
        account = albert.confirm_verification(
            flow.phone,
            flow.installation_id,
            flow.login_id,
            "123456",
            clock_offset=flow.clock_offset,
            config=CONFIG,
            http=http,
        )
    assert installations == [flow.installation_id] * 3
    assert UUID(flow.installation_id).version == 1
    assert account.subject == "signed-principal"
    assert json.loads(account.credential) == {
        "refresh_token": "synthetic-grant",
        "subject": "signed-principal",
    }
    assert "synthetic-grant" not in repr(account)


@pytest.mark.parametrize(
    ("status", "reason"),
    [(429, "login_rate_limited"), (400, "phone_rejected"), (503, "provider_unavailable")],
)
def test_sms_start_returns_sanitized_failure_without_retry(status: int, reason: str) -> None:
    attempts = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.method == "GET":
            return httpx.Response(404)
        attempts += 1
        return httpx.Response(status, text="sensitive provider response")

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as http,
        pytest.raises(albert.AlbertError, match=rf"^{reason}$"),
    ):
        albert.start_verification(PHONE, config=CONFIG, http=http)
    assert attempts == 1


@pytest.mark.parametrize("subject", [None, " "])
def test_sms_login_rejects_non_ascii_code_and_missing_account_identity(subject: str | None) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content).get("code") == "123456"
        return tokens(subject=subject)

    installation = "7e2332c0-a630-11f1-8760-0242ac120002"
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(albert.AlbertError, match="^otp_rejected$"):
            albert.confirm_verification(
                PHONE, installation, "login", "１２３４５６", config=CONFIG, http=http
            )
        with pytest.raises(albert.AlbertError, match="^provider_response_invalid$"):
            albert.confirm_verification(
                PHONE, installation, "login", "123456", config=CONFIG, http=http
            )


def test_archive_paginates_and_rechecks_both_streams_after_rotation() -> None:
    archived = [f"archive/{number}" for number in range(23)]
    saved: list[str] = []
    seen: list[tuple[str, int]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/refresh-token":
            assert request.method == "POST"
            assert [json.loads(value) for value in saved] == [
                {"subject": "sample-account", "refresh_inflight": True},
            ]
            assert json.loads(request.content) == {"token": "synthetic-refresh"}
            return tokens()
        assert request.url.host == "aczloyalty-service-app-prod.delhaize.eu"
        assert request.method == "GET" and request.url.path == "/customer/purchases/v2"
        assert request.headers.get("Authorization", "").startswith("Bearer e30.")
        assert request.headers.get("AppVersion") == "7.4.0"
        assert json.loads(saved[-1]) == {
            "refresh_token": "synthetic-rotated",
            "subject": "sample-account",
        }
        params = request.url.params
        assert params.get("pageSize") == "20"
        page = int(params.get("pageNumber", "0"))
        stream = params.get("archived", "current")
        seen.append((stream, page))
        return (
            listing(archived[(page - 1) * 20 : page * 20], page=page, total=23)
            if (stream == "true")
            else listing([])
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = albert.AlbertClient(CREDENTIAL, saved.append, config=CONFIG, http=http)
        first = client.page(1)
        assert first.get("totalCount") == 23
        assert first.get("receipts") == [{"receiptNumber": item} for item in archived[:20]]
        assert client.page(2).get("receipts") == [{"receiptNumber": item} for item in archived[20:]]
        assert client.page(1) == first
    assert seen == [("current", 1), ("true", 1), ("true", 2), ("current", 1), ("true", 1)]
    assert len(saved) == 2


def test_streams_deduplicate_and_fail_when_archive_moves_between_reads() -> None:
    archive_reads = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal archive_reads
        if request.url.path == "/auth/refresh-token":
            return tokens()
        if request.url.params.get("archived") == "true":
            archive_reads += 1
            return listing(["same", "older" if archive_reads == 1 else "newer"])
        return listing(["latest", "same"])

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = albert.AlbertClient(CREDENTIAL, lambda _: None, config=CONFIG, http=http)
        assert client.page(1) == {
            "receipts": [
                {"receiptNumber": "latest"},
                {"receiptNumber": "same"},
                {"receiptNumber": "older"},
            ],
            "totalCount": 3,
        }
        with pytest.raises(albert.AlbertError, match="^history_incomplete$"):
            client.page(1)


@pytest.mark.parametrize(
    "last_page",
    [
        listing(["receipt-0"], page=2, total=21),
        listing(["new"], page=2, total=22),
        listing([], page=2, total=21),
    ],
)
def test_archive_rejects_duplicate_or_incomplete_paging(last_page: httpx.Response) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/refresh-token":
            return tokens()
        if request.url.params.get("archived") is None:
            return listing([])
        if request.url.params.get("pageNumber") == "1":
            return listing([f"receipt-{number}" for number in range(20)], total=21)
        return last_page

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = albert.AlbertClient(CREDENTIAL, lambda _: None, config=CONFIG, http=http)
        with pytest.raises(albert.AlbertError, match="^history_incomplete$"):
            client.page(1)


def test_detail_checks_identity_and_keeps_original_decimal_bytes() -> None:
    original = b'{"receiptNumber":"cz/1 +2","total":19.9900}'

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/refresh-token":
            return tokens()
        assert request.url.path == "/customer/purchase/detail"
        if request.url.params.get("receiptNumber") == "cz/1 +2":
            return httpx.Response(200, content=original)
        return httpx.Response(200, json={"receiptNumber": "other"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = albert.AlbertClient(CREDENTIAL, lambda _: None, config=CONFIG, http=http)
        detail = client.detail("cz/1 +2")
        assert isinstance(detail, ProviderPayload) and detail.raw == original
        total = detail.get("total")
        assert isinstance(total, Decimal) and str(total) == "19.9900"
        with pytest.raises(albert.AlbertError, match="^receipt_identity_mismatch$"):
            client.detail("cz/2")


@pytest.mark.parametrize("failure", ["persistence", "timeout", "wrong_subject", "provider"])
def test_failed_rotation_never_replays_refresh_or_reads_receipts(failure: str) -> None:
    requests = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        assert request.url.path == "/auth/refresh-token"
        if failure == "timeout":
            raise httpx.ReadTimeout("sensitive URL", request=request)
        if failure == "provider":
            return httpx.Response(503)
        return tokens(subject="wrong-subject" if failure == "wrong_subject" else "sample-account")

    durable = CREDENTIAL

    def save(credential: str) -> None:
        nonlocal durable
        if not json.loads(credential).get("refresh_inflight"):
            assert failure == "persistence"
            raise RuntimeError("sensitive credential")
        durable = credential

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = albert.AlbertClient(CREDENTIAL, save, config=CONFIG, http=http)
        for _ in range(2):
            with pytest.raises(albert.AlbertError, match="^auth_required$"):
                client.page(1)
        with pytest.raises(albert.AlbertError, match="^auth_required$"):
            albert.AlbertClient(durable, save, config=CONFIG, http=http).page(1)
    assert requests == 1
    assert json.loads(durable) == {"subject": "sample-account", "refresh_inflight": True}


def test_unauthorized_get_renews_once_and_stops_on_second_rejection() -> None:
    requests: list[str] = []
    saved: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/auth/refresh-token":
            rotation = requests.count("/auth/refresh-token")
            previous = "synthetic-refresh" if rotation == 1 else "synthetic-rotated-1"
            assert json.loads(request.content) == {"token": previous}
            assert json.loads(saved[-1]).get("refresh_inflight") is True
            return tokens(refresh=f"synthetic-rotated-{rotation}")
        assert len(saved) == requests.count("/auth/refresh-token") * 2
        return httpx.Response(401)

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = albert.AlbertClient(CREDENTIAL, saved.append, config=CONFIG, http=http)
        for _ in range(2):
            with pytest.raises(albert.AlbertError, match="^auth_required$"):
                client.detail("cz-1")
    assert requests == ["/auth/refresh-token", "/customer/purchase/detail"] * 2


def test_transient_get_retries_tick_heartbeat_without_refresh_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    ticks = 0

    def heartbeat() -> None:
        nonlocal ticks
        ticks += 1

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path == "/auth/refresh-token":
            assert ticks == 1
            return tokens()
        attempts += 1
        assert ticks > attempts
        if attempts == 1:
            return httpx.Response(503)
        if attempts == 2:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"receiptNumber": "receipt-1"})

    monkeypatch.setattr(albert.time, "sleep", lambda _: None)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = albert.AlbertClient(
            CREDENTIAL, lambda _: None, config=CONFIG, http=http, heartbeat=heartbeat
        )
        assert client.detail("receipt-1").get("receiptNumber") == "receipt-1"
    assert attempts == 3


def test_provider_redirect_does_not_send_credentials_to_new_origin() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "aczloyalty-auth-service-v2-prod.delhaize.eu"
        return httpx.Response(302, headers={"Location": "https://example.invalid/collect"})

    with (
        httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True) as http,
        pytest.raises(albert.AlbertError, match="^auth_required$"),
    ):
        albert.AlbertClient(CREDENTIAL, lambda _: None, config=CONFIG, http=http).page(1)


def test_history_snapshot_archives_exact_amounts() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/refresh-token":
            return tokens()
        if request.url.params.get("archived") is None:
            return listing([])
        return httpx.Response(
            200,
            content=(
                b'{"receipts":[{"receiptNumber":"receipt","receiptTotal":12.3400}],'
                b'"page":{"number":1,"size":20,"total":1,"totalElements":1}}'
            ),
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = albert.AlbertClient(CREDENTIAL, lambda _: None, config=CONFIG, http=http)
        result = client.page(1)
        assert result.get("receipts") == [
            {"receiptNumber": "receipt", "receiptTotal": Decimal("12.3400")}
        ]
        raw = result.raw if isinstance(result, ProviderPayload) else json.dumps(result)
        decoded = json.loads(raw).get("receipts", [])[0]
        assert Decimal(str(decoded.get("receiptTotal"))) == Decimal("12.3400")


def test_worker_exit_during_refresh_cannot_replay_credential_on_restart() -> None:
    durable = CREDENTIAL
    requests = 0

    def save(credential: str) -> None:
        nonlocal durable
        durable = credential

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        assert request.url.path == "/auth/refresh-token"
        raise SystemExit

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(SystemExit):
            albert.AlbertClient(CREDENTIAL, save, config=CONFIG, http=http).page(1)
        with pytest.raises(albert.AlbertError, match="^auth_required$"):
            albert.AlbertClient(durable, save, config=CONFIG, http=http).page(1)
    assert requests == 1
    assert "refresh_token" not in json.loads(durable)


def test_uncommitted_refresh_marker_blocks_provider_request() -> None:
    def save(_: str) -> None:
        raise RuntimeError("database write failed")

    def handle(_: httpx.Request) -> httpx.Response:
        pytest.fail("requested refresh before durable invalidation")

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as http,
        pytest.raises(albert.AlbertError, match="^auth_required$"),
    ):
        albert.AlbertClient(CREDENTIAL, save, config=CONFIG, http=http).page(1)


def test_success_restores_credential_when_provider_returns_same_refresh_token() -> None:
    saved: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/refresh-token":
            return tokens(refresh="synthetic-refresh")
        assert json.loads(saved[-1]) == json.loads(CREDENTIAL)
        return listing([])

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = albert.AlbertClient(CREDENTIAL, saved.append, config=CONFIG, http=http)
        assert client.page(1).get("totalCount") == 0
    assert len(saved) == 2


@pytest.mark.parametrize("retry_after", ["3600", "Tue, 14 Nov 2023 22:14:00 GMT"])
def test_retry_after_over_budget_stops_without_sleep_or_retry(
    retry_after: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = 0
    sleeps: list[float] = []

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path == "/auth/refresh-token":
            return tokens()
        attempts += 1
        return httpx.Response(429, headers={"Retry-After": retry_after})

    monkeypatch.setattr(albert.time, "time", lambda: 1_700_000_000.0)
    monkeypatch.setattr(albert.time, "sleep", sleeps.append)
    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as http,
        pytest.raises(albert.AlbertError, match="^rate_limited$"),
    ):
        client = albert.AlbertClient(CREDENTIAL, lambda _: None, config=CONFIG, http=http)
        client.detail("receipt-1")
    assert attempts == 1
    assert sleeps == []
