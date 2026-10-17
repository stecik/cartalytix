"""Accent-insensitive text matching shared by receipt filters and suggestions."""

from sqlite3 import Connection as SQLiteConnection
from unicodedata import combining, normalize

from sqlalchemy import ColumnElement, SQLColumnExpression, Text, bindparam, func

SEARCH_FUNCTION = "cartalytix_search_text"
# PostgreSQL forbids non-immutable functions in index expressions; unaccent() is only stable,
# so the wrapper pins the dictionary and is declared immutable.
SEARCH_FUNCTION_DDL = f"""
CREATE OR REPLACE FUNCTION {SEARCH_FUNCTION}(value text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
AS $$ SELECT lower(public.unaccent('public.unaccent'::regdictionary, value)) $$
"""


def fold(value: str) -> str:
    return "".join(
        character for character in normalize("NFKD", value) if not combining(character)
    ).lower()


def register_sqlite(connection: SQLiteConnection, _record: object) -> None:
    connection.create_function(SEARCH_FUNCTION, 1, fold, deterministic=True)


def search_text(value: SQLColumnExpression[str]) -> ColumnElement[str]:
    return func.cartalytix_search_text(value, type_=Text)


def like_pattern(term: str, *, before: str = "%", after: str = "%") -> ColumnElement[str]:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return search_text(bindparam(None, f"{before}{escaped}{after}", type_=Text))


def matches(column: SQLColumnExpression[str], term: str) -> ColumnElement[bool]:
    return search_text(column).like(like_pattern(term), escape="\\")
