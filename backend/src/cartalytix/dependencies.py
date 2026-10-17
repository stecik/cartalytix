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
            )
        )
        if user is not None:
            return user
    raise HTTPException(401, "authentication_required")


Owner = Annotated[User, Depends(current_user)]
