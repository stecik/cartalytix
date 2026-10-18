"""Administrator account management: the administrator never sees purchase data."""

from secrets import token_urlsafe
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import SecretStr
from sqlalchemy import Engine, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cartalytix.connection_routes import _manage_connection
from cartalytix.dependencies import Admin, Db
from cartalytix.models import (
    OAuthCode,
    OAuthGrant,
    OAuthToken,
    ProviderConnection,
    User,
    UserSession,
)
from cartalytix.schemas import (
    AdminUserCreateInput,
    AdminUserOutput,
    AdminUserPasswordOutput,
    AdminUsersOutput,
    AdminUserUpdateInput,
)
from cartalytix.security import PASSWORD_RULE, hash_password, is_strong_password

BOOTSTRAP_EMAIL = "admin@example.com"

router = APIRouter(prefix="/api/v1/admin/users", tags=["admin"])


def bootstrap_admin(engine: Engine, password: SecretStr | None) -> None:
    """Create the first-start administrator while the instance has no account at all."""
    with Session(engine) as db:
        if db.scalar(select(User.id).limit(1)) is not None:
            return
        try:
            initial_password = password.get_secret_value() if password is not None else ""
            password_hash = hash_password(initial_password)
        except ValueError as exc:
            raise RuntimeError(
                f"Set CARTALYTIX_BOOTSTRAP_PASSWORD before the first startup. {PASSWORD_RULE}"
            ) from exc
        db.add(
            User(
                email=BOOTSTRAP_EMAIL,
                password_hash=password_hash,
                is_admin=True,
                must_change_password=True,
            )
        )
        try:
            db.commit()
        except IntegrityError:
            # Another process created it concurrently.
            db.rollback()


def generated_password() -> str:
    # About half of all draws contain "-" or "_", the special character the policy requires.
    while not is_strong_password(password := token_urlsafe(15)):
        pass
    return password


def revoke_access(db: Session, user_id: UUID) -> None:
    """End every browser session and MCP authorization of one account."""
    grants = select(OAuthGrant.id).where(OAuthGrant.owner_id == user_id)
    db.execute(delete(OAuthToken).where(OAuthToken.grant_id.in_(grants)))
    db.execute(delete(OAuthCode).where(OAuthCode.owner_id == user_id))
    db.execute(delete(OAuthGrant).where(OAuthGrant.owner_id == user_id))
    db.execute(delete(UserSession).where(UserSession.owner_id == user_id))


def managed_user(db: Session, identifier: UUID) -> User:
    user = db.get(User, identifier)
    if user is None:
        raise HTTPException(404, "user_not_found")
    if user.is_admin:
        raise HTTPException(403, "admin_protected")
    return user


@router.get("", response_model=AdminUsersOutput)
def users(_admin: Admin, db: Db) -> AdminUsersOutput:
    rows = db.scalars(select(User).order_by(User.is_admin.desc(), User.email).limit(500))
    return AdminUsersOutput(items=[AdminUserOutput.model_validate(row) for row in rows])


@router.post("", response_model=AdminUserPasswordOutput, status_code=201)
def create_user(payload: AdminUserCreateInput, _admin: Admin, db: Db) -> AdminUserPasswordOutput:
    email = str(payload.email).strip().casefold()
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(409, "email_taken")
    password = generated_password()
    user = User(email=email, password_hash=hash_password(password), must_change_password=True)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "email_taken") from None
    return AdminUserPasswordOutput(user=AdminUserOutput.model_validate(user), password=password)


@router.post("/{identifier}/reset-password", response_model=AdminUserPasswordOutput)
def reset_password(identifier: UUID, _admin: Admin, db: Db) -> AdminUserPasswordOutput:
    user = managed_user(db, identifier)
    password = generated_password()
    user.password_hash = hash_password(password)
    user.must_change_password = True
    revoke_access(db, user.id)
    db.commit()
    return AdminUserPasswordOutput(user=AdminUserOutput.model_validate(user), password=password)


@router.patch("/{identifier}", response_model=AdminUserOutput)
def update_user(
    identifier: UUID, payload: AdminUserUpdateInput, _admin: Admin, db: Db
) -> AdminUserOutput:
    user = managed_user(db, identifier)
    user.is_disabled = payload.is_disabled
    if payload.is_disabled:
        revoke_access(db, user.id)
    db.commit()
    return AdminUserOutput.model_validate(user)


@router.delete("/{identifier}", status_code=204)
def delete_user(identifier: UUID, request: Request, _admin: Admin, db: Db) -> None:
    user = managed_user(db, identifier)
    user_id = user.id
    # Lock the account out first, so a failed connection removal can simply be retried.
    user.is_disabled = True
    revoke_access(db, user_id)
    db.commit()
    connections = db.execute(
        select(ProviderConnection.id, ProviderConnection.provider).where(
            ProviderConnection.owner_id == user_id
        )
    ).all()
    for connection_id, provider in connections:
        _manage_connection(provider, connection_id, "remove", request, user_id, db)
    db.execute(delete(User).where(User.id == user_id))
    db.commit()
