"""Receipt list paging and PDF download from globus.cz with a stored cookie session."""

import hashlib
import os
import time
from collections import Counter
from collections.abc import Callable
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from cartalytix.export import ExportError, write_json
from mixit.export import shape

from .receipt_pdf import CENT, parse, pdf_text

LIST_URL = "https://www.globus.cz/api/v1/gsoa/customer/receipts"
PAGE_SIZE = 20
MAX_PAGES = 10000


class SessionError(ExportError):
    pass


class Client:
    """Sends the session cookie only to the fixed www.globus.cz receipt API."""

    def __init__(
        self,
        cookie: str,
        *,
        http: httpx.Client | None = None,
        delay: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.headers = {"Cookie": f"oidc_session={cookie}"}
        self.http = http or httpx.Client(timeout=30, follow_redirects=False)
        self.owns_http = http is None
        self.delay = delay
        self.sleep = sleep

    def close(self) -> None:
        if self.owns_http:
            self.http.close()

    def get(self, url: str, params: dict[str, str] | None = None) -> httpx.Response:
        for attempt in range(4):
            self.sleep(self.delay)
            try:
                response = self.http.get(url, params=params, headers=self.headers)
            except httpx.TransportError:
                if attempt == 3:
                    raise ExportError(
                        "API connection failed after bounded retries"
                    ) from None
                continue
            if response.status_code in {401, 403} or response.is_redirect:
                raise SessionError(
                    "Globus session rejected or expired; run: uv run globus login"
                )
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 3:
                self.sleep(max(self.delay, 2) * (2**attempt))
                continue
            if response.status_code != 200:
                raise ExportError(
                    f"Globus API HTTP {response.status_code}; no response body logged"
                )
            return response
        raise ExportError("API retry limit reached")

    def page(self, number: int) -> dict[str, Any]:
        response = self.get(
            LIST_URL,
            {
                "pageNumber": str(number),
                "pageSize": str(PAGE_SIZE),
                "totalFrom": "0",
                "includeGlobusOnline": "true",
            },
        )
        try:
            result = response.json()
        except ValueError:
            raise ExportError("Receipt list was not JSON") from None
        if not isinstance(result, dict):
            raise ExportError("Receipt list returned unexpected schema")
        return result

    def pdf(self, receipt_id: str) -> bytes:
        data = self.get(f"{LIST_URL}/{quote(receipt_id, safe='')}/pdf").content
        if not data.startswith(b"%PDF"):
            raise ExportError("Receipt detail was not a PDF")
        return data


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(data)
    os.replace(temp, path)


def merge(row: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    """List fields fill what the PDF lacks; a differing list total is flagged, not used."""
    issues = set(parsed.get("issues", []))
    listed = row.get("total")
    if isinstance(listed, int | float) and parsed.get("total") is not None:
        printed = Decimal(parsed.get("total", "0"))
        # The list was seen rounding the total to whole crowns (526.80 -> 527).
        accepted = {printed, printed.quantize(Decimal(1), ROUND_HALF_UP)}
        if Decimal(str(listed)).quantize(CENT) not in accepted:
            issues.add("list_total_mismatch")
    return {
        **parsed,
        "id": str(row.get("id")),
        "date": parsed.get("date") or row.get("dateTime"),
        "house": parsed.get("house") or row.get("house"),
        "store": row.get("houseName"),
        "issues": sorted(issues),
    }


def collect(
    client: Client,
    destination: Path,
    *,
    refresh_details: bool = False,
    extract: Callable[[bytes], str] = pdf_text,
    progress: Callable[[str], None] = lambda text: None,
) -> dict[str, Any]:
    listed: dict[str, dict[str, Any]] = {}
    list_shape: Counter[str] = Counter()
    total_count: Any = None
    for number in range(1, MAX_PAGES + 1):
        page = client.page(number)
        write_json(destination / "raw" / "list" / f"{number:04d}.json", page)
        shape(page, found=list_shape)
        rows = [
            r for r in page.get("receipts") or [] if isinstance(r, dict) and r.get("id")
        ]
        total_count = page.get("totalCount")
        for row in rows:
            listed.setdefault(str(row.get("id")), row)
        if not rows or (isinstance(total_count, int) and len(listed) >= total_count):
            break
    receipts: list[dict[str, Any]] = []
    downloaded = cached = failures = 0
    for receipt_id, row in listed.items():
        path = (
            destination
            / "raw"
            / "pdf"
            / (hashlib.sha256(receipt_id.encode()).hexdigest() + ".pdf")
        )
        if path.exists() and not refresh_details:
            data = path.read_bytes()
            cached += 1
        else:
            try:
                data = client.pdf(receipt_id)
            except SessionError:
                raise
            except ExportError:
                failures += 1
                continue
            write_bytes(path, data)
            downloaded += 1
            progress(f"Účtenka {downloaded + cached}/{len(listed)}")
        receipts.append(merge(row, parse(extract(data))))
    complete = failures == 0 and (
        not isinstance(total_count, int) or len(listed) >= total_count
    )
    write_json(
        destination / ("receipts.json" if complete else "receipts.partial.json"),
        receipts,
    )
    report = {
        "complete": complete,
        "unique_receipts": len(listed),
        "total_count": total_count,
        "downloaded_details": downloaded,
        "cached_details": cached,
        "detail_failures": failures,
        "issues": dict(Counter(i for r in receipts for i in r.get("issues", []))),
        "list_schema": dict(sorted(list_shape.items())),
    }
    write_json(destination / "report.json", report)
    return report
