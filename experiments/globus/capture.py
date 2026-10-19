"""Turns observed globus.cz traffic into value-free flow, token data and receipt templates."""

import json
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from boltfood.capture import context_headers, find_key, keys, query, scheme
from mixit.export import shape
from wolt.capture import has_rows

STATIC_SUFFIXES = (
    ".js",
    ".css",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
)
RECEIPT_WORDS = ("receipt", "uctenk", "purchase", "nakup", "transaction", "bill")


def globus_host(host: str) -> bool:
    return host == "globus.cz" or host.endswith(".globus.cz")


def api_url(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme == "https" and not parsed.path.lower().endswith(
        STATIC_SUFFIXES
    )


def body_fields(body: str | None, content_type: str) -> dict[str, Any]:
    if "json" in content_type:
        try:
            data = json.loads(body or "")
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}
    if "form" in content_type:
        return dict(parse_qsl(body or ""))
    return {}


def cookie_names(headers: dict[str, str]) -> list[str]:
    value = next((v for k, v in headers.items() if k.lower() == "cookie"), "")
    return sorted(
        {part.split("=", 1)[0].strip() for part in value.split(";") if "=" in part}
    )


class Recorder:
    """Keeps tokens in memory; the flow log holds only hosts, paths and names."""

    def __init__(self) -> None:
        self.flow: list[dict[str, Any]] = []
        self.samples: dict[str, dict[str, int]] = {}
        self.raw: dict[str, Any] = {}
        self.receipts: dict[str, dict[str, Any]] = {}
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
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if not api_url(url) or method not in {"GET", "POST"}:
            return
        # Foreign hosts matter only as possible identity providers (JSON or POST).
        if not globus_host(host) and data is None and method != "POST":
            return
        content_type = next(
            (v for k, v in headers.items() if k.lower() == "content-type"), ""
        ).split(";", 1)[0]
        sent = body_fields(body, content_type)
        self.flow.append(
            {
                "method": method,
                "host": host,
                "path": parsed.path,
                "status": status,
                "auth": scheme(headers),
                "cookies": cookie_names(headers),
                "query_keys": sorted(query(url)),
                "body_keys": sorted(sent),
                "content_type": content_type,
                "response_keys": keys(data),
            }
        )
        access, refresh = (
            find_key(data, "access_token"),
            find_key(data, "refresh_token"),
        )
        if status == 200 and isinstance(access, str) and isinstance(refresh, str):
            field = next(
                (
                    k
                    for k, v in sent.items()
                    if self.refresh_token and v == self.refresh_token
                ),
                None,
            )
            if field is not None:
                # Observed refresh: replay exactly this body shape later.
                self.refresh = {
                    "url": f"https://{host}{parsed.path}",
                    "content_type": content_type,
                    "fields": {k: v for k, v in sent.items() if k != field},
                    "token_field": field,
                }
            self.access_token, self.refresh_token = access, refresh
            return
        operation = str(sent.get("operationName") or "")
        target = f"{parsed.path} {operation}".lower()
        if (
            status == 200
            and globus_host(host)
            and any(word in target for word in RECEIPT_WORDS)
            and has_rows(data)
        ):
            key = f"{method} {host}{parsed.path} {operation}".rstrip()
            self.samples[key] = dict(sorted(shape(data).items()))
            self.raw[key] = data
            self.receipts[key] = {
                "base": f"https://{host}",
                "path": parsed.path,
                "method": method,
                "query": query(url),
                "body": sent,
                "headers": context_headers(headers),
            }
