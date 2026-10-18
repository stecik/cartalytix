"""Turns observed wolt.com traffic into token data, replay templates and value-free schemas."""

from typing import Any
from urllib.parse import parse_qsl, urlsplit

from boltfood.capture import context_headers, find_key, keys, query, scheme
from mixit.export import shape

TOKEN_PATH = "/v1/wauth2/access_token"
STATIC_HOSTS = ("wolt-com-static-assets.wolt.com", "imageproxy.wolt.com")


def api_url(url: str) -> bool:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    return (
        parsed.scheme == "https"
        and (host == "wolt.com" or host.endswith(".wolt.com"))
        and host not in STATIC_HOSTS
        and not parsed.path.endswith((".js", ".css", ".png", ".svg", ".woff2"))
    )


def form_keys(body: str | None) -> list[str]:
    return sorted(dict(parse_qsl(body or "")))


def has_rows(data: Any) -> bool:
    """True when a response holds a non-empty list of objects (an order list candidate)."""
    if isinstance(data, list):
        return any(isinstance(item, dict) for item in data)
    if isinstance(data, dict):
        return any(has_rows(value) for value in data.values())
    return False


class Recorder:
    """Keeps tokens in memory, templates for order requests, and only names in the flow log."""

    def __init__(self) -> None:
        self.flow: list[dict[str, Any]] = []
        self.samples: dict[str, dict[str, int]] = {}
        self.raw: dict[str, Any] = {}
        self.orders: dict[str, dict[str, Any]] = {}
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.refresh: dict[str, Any] | None = None

    def observe(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: str | None,
        status: int,
        data: Any,
    ) -> None:
        if not api_url(url) or method not in {"GET", "POST"}:
            return
        parsed = urlsplit(url)
        path = parsed.path
        content_type = next(
            (v for k, v in headers.items() if k.lower() == "content-type"), ""
        )
        self.flow.append(
            {
                "method": method,
                "host": parsed.hostname,
                "path": path,
                "status": status,
                "auth": scheme(headers),
                "query_keys": sorted(query(url)),
                "body_keys": form_keys(body) if "form" in content_type else [],
                "content_type": content_type.split(";", 1)[0],
                "response_keys": keys(data),
            }
        )
        if path.endswith(TOKEN_PATH) and status == 200:
            access, refresh = (
                find_key(data, "access_token"),
                find_key(data, "refresh_token"),
            )
            if isinstance(access, str) and isinstance(refresh, str):
                sent = dict(parse_qsl(body or ""))
                field = next(
                    (k for k, v in sent.items() if v == self.refresh_token), None
                )
                if field is not None:
                    # Observed refresh: replay exactly this form shape later.
                    self.refresh = {
                        "url": f"https://{parsed.hostname}{path}",
                        "content_type": content_type.split(";", 1)[0],
                        "fields": {k: v for k, v in sent.items() if k != field},
                        "token_field": field,
                        "observed": True,
                    }
                self.access_token, self.refresh_token = access, refresh
            return
        if status == 200 and "order" in path.lower() and has_rows(data):
            key = f"{method} {parsed.hostname}{path}"
            self.samples[key] = dict(sorted(shape(data).items()))
            self.raw[key] = data
            self.orders[key] = {
                "base": f"https://{parsed.hostname}",
                "path": path,
                "method": method,
                "query": query(url),
                "headers": context_headers(headers),
            }

    def complete(self) -> bool:
        return bool(self.refresh_token and self.orders)

    def refresh_template(self) -> dict[str, Any]:
        # Standard OAuth refresh grant until the web app is seen refreshing by itself.
        return self.refresh or {
            "url": "https://authentication.wolt.com" + TOKEN_PATH,
            "content_type": "application/x-www-form-urlencoded",
            "fields": {"grant_type": "refresh_token"},
            "token_field": "refresh_token",
            "observed": False,
        }
