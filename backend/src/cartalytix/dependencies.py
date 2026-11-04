from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from cartalytix.models import User, UserSession
from cartalytix.security import token_hash
from cartalytix.settings import Settings

COOKIE_NAME = "cartalytix_session"
SLIDE_INTERVAL = timedelta(hours=1)


def get_db(request: Request) -> Generator[Session]:
    with Session(request.app.state.engine) as db:
        yield db


Db = Annotated[Session, Depends(get_db)]


def set_session_cookie(
    response: Response, token: str, config: Settings, lifetime: timedelta | None
) -> None:
    """Without a lifetime the cookie lasts until the browser closes."""
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=None if lifetime is None else int(lifetime.total_seconds()),
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
        path="/",
    )


def current_user(request: Request, response: Response, db: Db) -> User:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        now = datetime.now(UTC)
        row = db.execute(
            select(User, UserSession)
            .join(UserSession, UserSession.owner_id == User.id)
            .where(
                UserSession.token_hash == token_hash(token),
                UserSession.expires_at > now,
                User.is_disabled.is_(False),
            )
        ).one_or_none()
        if row is not None:
            user, session = row
            if session.remember:
                _slide_session(request, response, db, session, token, now)
            request.state.diagnostic_owner_id = user.id
            return user
    raise HTTPException(401, "authentication_required")


def _slide_session(
    request: Request,
    response: Response,
    db: Session,
    session: UserSession,
    token: str,
    now: datetime,
) -> None:
    # Extended at most once per interval, so ordinary requests do not write.
    config: Settings = request.app.state.settings
    lifetime = timedelta(days=config.remember_days)
    expires_at = session.expires_at.replace(tzinfo=session.expires_at.tzinfo or UTC)
    if expires_at - now > lifetime - SLIDE_INTERVAL:
        return
    session.expires_at = now + lifetime
    db.commit()
    set_session_cookie(response, token, config, lifetime)


# Any signed-in account, including one that still has to replace its credentials.
SessionUser = Annotated[User, Depends(current_user)]


def active_user(user: SessionUser) -> User:
    if user.must_change_password:
        raise HTTPException(403, "password_change_required")
    return user


ActiveUser = Annotated[User, Depends(active_user)]


def data_owner(user: ActiveUser) -> User:
    if user.is_admin:
        raise HTTPException(403, "admin_forbidden")
    return user


Owner = Annotated[User, Depends(data_owner)]


def administrator(user: ActiveUser) -> User:
    if not user.is_admin:
        raise HTTPException(403, "admin_required")
    return user


Admin = Annotated[User, Depends(administrator)]


def lock_owner(db: Session, owner_id: UUID) -> None:
    # Serializes an owner's writes across web/MCP workers.
    db.execute(select(User.id).where(User.id == owner_id).with_for_update()).one()
