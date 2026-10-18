"""Accent-insensitive text matching shared by receipt filters and suggestions."""

from datetime import UTC, datetime
from sqlite3 import Connection as SQLiteConnection
from unicodedata import combining, normalize
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, SQLColumnExpression, Text, bindparam, func, or_

SEARCH_FUNCTION = "cartalytix_search_text"
# PostgreSQL forbids non-immutable functions in index expressions; unaccent() is only stable,
# so the wrapper pins the dictionary and is declared immutable.
SEARCH_FUNCTION_DDL = f"""
CREATE OR REPLACE FUNCTION {SEARCH_FUNCTION}(value text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
AS $$ SELECT lower(public.unaccent('public.unaccent'::regdictionary, value)) $$
"""


# Connector-generated rows store a slug that the UI translates; search matches its labels.
SYSTEM_NAMES = {
    "shipping_and_fees": ("Shipping and fees", "Doprava a poplatky"),
    "discounts_and_credits": ("Discounts and credits", "Slevy a kredity"),
}


def fold(value: str) -> str:
    return "".join(
        character for character in normalize("NFKD", value) if not combining(character)
    ).lower()


def _prague_time(value: str | None) -> str | None:
    """SQLite stand-in for PostgreSQL timezone(); stored values are naive UTC."""
    if value is None:
        return None
    moment = datetime.fromisoformat(value).replace(tzinfo=UTC).astimezone(ZoneInfo("Europe/Prague"))
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def register_sqlite(connection: SQLiteConnection, _record: object) -> None:
    connection.create_function(SEARCH_FUNCTION, 1, fold, deterministic=True)
    connection.create_function("cartalytix_prague_time", 1, _prague_time, deterministic=True)


def search_text(value: SQLColumnExpression[str]) -> ColumnElement[str]:
    return func.cartalytix_search_text(value, type_=Text)


def like_pattern(term: str, *, before: str = "%", after: str = "%") -> ColumnElement[str]:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return search_text(bindparam(None, f"{before}{escaped}{after}", type_=Text))


def matches(column: SQLColumnExpression[str], term: str) -> ColumnElement[bool]:
    return search_text(column).like(like_pattern(term), escape="\\")


def system_names(term: str) -> list[str]:
    needle = fold(term)
    return [
        slug
        for slug, labels in SYSTEM_NAMES.items()
        if any(needle in fold(label) for label in (slug, *labels))
    ]


def name_matches(column: SQLColumnExpression[str], term: str) -> ColumnElement[bool]:
    slugs = system_names(term)
    return or_(matches(column, term), column.in_(slugs)) if slugs else matches(column, term)
