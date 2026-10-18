"""Owner-scoped search suggestions for receipt items and merchants."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import ColumnElement, Float, case, func, literal, or_, select, text
from sqlalchemy.orm import InstrumentedAttribute, Session

from cartalytix.dependencies import Db, Owner
from cartalytix.models import ProviderConnection, Receipt, ReceiptItem
from cartalytix.schemas import SuggestionKind, SuggestionOutput, SuggestionsOutput
from cartalytix.search import like_pattern, search_text, system_names

MIN_TERM_LENGTH = 3
# word_similarity cut-off tuned on real receipt names: keeps "mozarela" -> "Mozzarella" (0.55)
# while dropping unrelated three-letter overlaps (0.4).
WORD_SIMILARITY_THRESHOLD = 0.5

router = APIRouter(prefix="/api/v1/receipts", tags=["receipts"])


def _suggest(
    db: Session,
    owner_id: UUID,
    column: InstrumentedAttribute[str],
    term: str,
    limit: int,
    fuzzy: bool,
) -> list[tuple[int, float, int, str]]:
    key = search_text(column)
    contains = key.like(like_pattern(term), escape="\\")
    rank = case(
        (key.like(like_pattern(term, before=""), escape="\\"), 3),
        (key.like(like_pattern(term, before="% "), escape="\\"), 2),
        (contains, 1),
        else_=0,
    )
    condition: ColumnElement[bool] = contains
    slugs = system_names(term) if column.class_ is ReceiptItem else []
    if slugs:
        contains = or_(contains, column.in_(slugs))
        condition = contains
    similarity: ColumnElement[float] = literal(0.0, Float)
    if fuzzy:
        needle = search_text(literal(term))
        condition = or_(contains, needle.op("<%")(key))
        similarity = func.word_similarity(needle, key, type_=Float)
    receipt_count = func.count(func.distinct(Receipt.id))
    statement = select(rank, similarity, receipt_count, column)
    if column.class_ is ReceiptItem:
        statement = statement.select_from(ReceiptItem).join(
            Receipt, ReceiptItem.receipt_id == Receipt.id
        )
    else:
        statement = statement.select_from(Receipt)
    statement = (
        statement.join(ProviderConnection, Receipt.connection_id == ProviderConnection.id)
        .where(ProviderConnection.owner_id == owner_id, condition)
        .group_by(column)
        .order_by(rank.desc(), similarity.desc(), receipt_count.desc(), column)
        .limit(limit)
    )
    return [
        (row_rank, row_similarity, count, name)
        for row_rank, row_similarity, count, name in db.execute(statement)
    ]


@router.get("/suggestions", response_model=SuggestionsOutput)
def receipt_suggestions(
    user: Owner,
    db: Db,
    q: Annotated[str, Query(min_length=MIN_TERM_LENGTH, max_length=100)],
    limit: Annotated[int, Query(ge=1, le=20)] = 8,
    kind: SuggestionKind | None = None,
) -> SuggestionsOutput:
    term = q.strip()
    if len(term) < MIN_TERM_LENGTH:
        return SuggestionsOutput(items=[])
    fuzzy = db.get_bind().dialect.name == "postgresql"
    if fuzzy:
        db.execute(
            text("SELECT set_config('pg_trgm.word_similarity_threshold', :value, true)"),
            {"value": str(WORD_SIMILARITY_THRESHOLD)},
        )
    sources = ((SuggestionKind.ITEM, ReceiptItem.name), (SuggestionKind.MERCHANT, Receipt.merchant))
    candidates = [
        (*row, source)
        for source, column in sources
        if kind in (None, source)
        for row in _suggest(db, user.id, column, term, limit, fuzzy)
    ]
    candidates.sort(key=lambda row: (-row[0], -row[1], -row[2], row[3]))
    return SuggestionsOutput(
        items=[
            SuggestionOutput(kind=source, text=name, receipt_count=count)
            for _rank, _similarity, count, name, source in candidates[:limit]
        ]
    )
