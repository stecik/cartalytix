"""Store only fixed diagnostics and stack locations, never exception/request values."""

import re
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import Engine, delete
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from cartalytix.models import DiagnosticEvent

HTTP_ERROR_CODES = frozenset(
    {"service_unavailable", "credentials_unavailable", "provider_unavailable", "rate_limited"}
)


def exception_context(error: BaseException) -> dict[str, Any]:
    frames = traceback.extract_tb(error.__traceback__, limit=-12)
    return {
        "exception_type": type(error).__name__[:100],
        "frames": [
            {"filename": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
            for frame in frames
        ],
    }


def issue_codes(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        code
        for code in value[:50]
        if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code)
    ]


def capture_error(
    engine: Engine,
    owner_id: UUID | None,
    component: Literal["api", "worker"],
    code: str,
    *,
    error: BaseException | None = None,
    route: str | None = None,
    status: int | None = None,
) -> None:
    context = exception_context(error) if error is not None else {}
    if route is not None:
        context["route"] = route
    if status is not None:
        context["status"] = status
    try:
        with Session(engine) as db, db.begin():
            db.execute(
                delete(DiagnosticEvent).where(
                    DiagnosticEvent.created_at < datetime.now(UTC) - timedelta(days=7)
                )
            )
            db.add(
                DiagnosticEvent(owner_id=owner_id, component=component, code=code, context=context)
            )
    except SQLAlchemyError:
        # Recording a database failure must not replace the original error response.
        pass
