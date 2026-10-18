"""Token renewal against Wolt's auth host; the refresh token is sent nowhere else."""

from collections.abc import Callable
from typing import Any

import httpx

from boltfood.capture import find_key
from cartalytix.export import ExportError


def renew(
    template: dict[str, Any],
    refresh_token: str,
    save_refresh: Callable[[str], None],
    *,
    http: httpx.Client | None = None,
) -> str:
    """Return a fresh access token; a rotated refresh token is persisted before returning."""
    url = str(template.get("url", ""))
    if not url.startswith("https://authentication.wolt.com/"):
        raise ExportError(
            "Refusing to send the refresh token outside authentication.wolt.com"
        )
    fields = {
        **template.get("fields", {}),
        template.get("token_field", "refresh_token"): refresh_token,
    }
    owned = http is None
    client = http or httpx.Client(timeout=30, follow_redirects=False)
    try:
        try:
            # A rotating refresh POST is never retried: a lost response may have consumed it.
            if template.get("content_type") == "application/json":
                response = client.post(
                    url, json=fields, headers={"Accept": "application/json"}
                )
            else:
                response = client.post(
                    url, data=fields, headers={"Accept": "application/json"}
                )
        except httpx.HTTPError:
            raise ExportError(
                "Token request failed; run login again if it repeats"
            ) from None
    finally:
        if owned:
            client.close()
    if response.status_code != 200:
        raise ExportError(f"Token HTTP {response.status_code}; no response body logged")
    try:
        data = response.json()
    except ValueError:
        raise ExportError("Token response was not JSON") from None
    access = find_key(data, "access_token")
    if not isinstance(access, str) or not access:
        raise ExportError("Token response without access token")
    rotated = find_key(data, "refresh_token")
    if isinstance(rotated, str) and rotated and rotated != refresh_token:
        save_refresh(rotated)
    return access
