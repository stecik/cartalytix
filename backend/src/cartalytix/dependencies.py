from collections.abc import Generator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from cartalytix.models import User, UserSession
from cartalytix.security import token_hash

COOKIE_NAME = "cartalytix_session"


def get_db(request: Request) -> Generator[Session]:
    with Session(request.app.state.engine) as db:
        yield db


Db = Annotated[Session, Depends(get_db)]


def current_user(request: Request, db: Db) -> User:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        user = db.scalar(
            select(User)
            .join(UserSession, UserSession.owner_id == User.id)
            .where(
                UserSession.token_hash == token_hash(token),
                UserSession.expires_at > datetime.now(UTC),
                User.is_disabled.is_(False),
            )
        )
        if user is not None:
            request.state.diagnostic_owner_id = user.id
            return user
    raise HTTPException(401, "authentication_required")


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
