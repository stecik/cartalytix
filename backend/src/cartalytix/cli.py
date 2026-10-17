import argparse
from getpass import getpass
from uuid import UUID

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from cartalytix.i18n import translate as _
from cartalytix.models import User
from cartalytix.providers.lidl.browser import link_connection, server_origin
from cartalytix.providers.lidl.client import LidlError
from cartalytix.security import hash_password
from cartalytix.settings import Settings


def provision_user(settings: Settings, email: str, password: str) -> None:
    normalized = validate_email(email, check_deliverability=False).normalized.casefold()
    password_hash = hash_password(password)
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        with Session(engine) as db:
            if db.scalar(select(User.id).where(User.email == normalized)) is not None:
                raise ValueError(_("User already exists"))
            db.add(User(email=normalized, password_hash=password_hash))
            db.commit()
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=_("Manage local application accounts"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("create-user", help=_("Create an application account"))
    link = commands.add_parser("link-lidl", help=_("Link a Lidl account using local Chrome"))
    link.add_argument("--server", required=True, type=server_origin)
    link.add_argument("--connection", required=True, type=UUID)
    args = parser.parse_args()
    if args.command == "link-lidl":
        email = input(_("Application email: ")).strip()
        password = getpass(_("Application password: "))
        try:
            link_connection(args.server, args.connection, email, password)
        except (LidlError, ValueError) as exc:
            parser.error(str(exc))
        except EOFError, KeyboardInterrupt:
            parser.exit(1, _("Login cancelled") + "\n")
        print(_("Lidl account linked. Start synchronization in the application."))
        return
    settings = Settings()
    email = input(_("Email: ")).strip()
    password = getpass(_("Password (at least 12 characters): "))
    confirmation = getpass(_("Confirm password: "))
    if password != confirmation:
        parser.error(_("Passwords do not match"))
    try:
        provision_user(settings, email, password)
    except (ValueError, EmailNotValidError) as exc:
        parser.error(str(exc))
    print(_("Account created"))
