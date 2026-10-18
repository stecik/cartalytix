"""Pure helpers that turn observed web-app traffic into replayable, sanitized templates."""

from typing import Any
from urllib.parse import parse_qsl, urlsplit

API_SUFFIX = ".live.boltsvc.net"
SKIPPED_HEADERS = {
    "accept",
    "accept-encoding",
    "accept-language",
    "connection",
    "content-length",
    "content-type",
    "host",
    "origin",
    "referer",
    "user-agent",
}


def api_path(url: str) -> str | None:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not host.endswith(API_SUFFIX):
        return None
    return parsed.path


def base_url(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.hostname}"


def query(url: str) -> dict[str, str]:
    return dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))


def context_headers(headers: dict[str, str]) -> dict[str, str]:
    """Non-secret custom headers the app sends; replayed so the API sees the same client."""
    return {
        name: value
        for name, value in ((name.lower(), value) for name, value in headers.items())
        if name not in SKIPPED_HEADERS
        and not name.startswith(("sec-", ":"))
        and not any(part in name for part in ("auth", "token", "cookie"))
        and len(value) <= 100
    }


def find_key(value: Any, key: str) -> Any:
    """First value stored under `key` anywhere in a JSON document."""
    if isinstance(value, dict):
        if key in value:
            return value.get(key)
        for child in value.values():
            found = find_key(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_key(child, key)
            if found is not None:
                return found
    return None


def find_path(
    value: Any, target: object, path: tuple[str, ...] = ()
) -> tuple[str, ...] | None:
    """Key path (lists as "[]") of the first scalar equal to `target`."""
    if isinstance(value, dict):
        for key, child in value.items():
            found = find_path(child, target, (*path, str(key)))
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_path(child, target, (*path, "[]"))
            if found is not None:
                return found
    elif value == target and not isinstance(value, bool):
        return path
    return None


def values_at(value: Any, path: tuple[str, ...]) -> list[Any]:
    """All scalars at a path produced by find_path, preserving document order."""
    if not path:
        return [value]
    head, rest = path[0], path[1:]
    if head == "[]":
        children = value if isinstance(value, list) else []
        return [found for child in children for found in values_at(child, rest)]
    if isinstance(value, dict) and head in value:
        return values_at(value.get(head), rest)
    return []


def keys(value: Any) -> list[str]:
    return sorted(value) if isinstance(value, dict) else []


def scheme(headers: dict[str, str]) -> str:
    value = next((v for k, v in headers.items() if k.lower() == "authorization"), "")
    return value.split(" ", 1)[0] if value else "none"


def token_header(headers: dict[str, str], token: str) -> tuple[str, str] | None:
    """(header name, prefix) carrying `token`, e.g. ("authorization", "Bearer ")."""
    for name, value in headers.items():
        if token and value.endswith(token):
            return name.lower(), value.removesuffix(token)
    return None
