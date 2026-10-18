"""Owner-scoped spending statistics for the overview dashboard."""

from calendar import monthrange
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from sqlalchemy import ColumnElement, Integer, Select, String, cast, func, select

from cartalytix.dependencies import Db, Owner
from cartalytix.models import Discount, ProviderConnection, Receipt, ReceiptItem
from cartalytix.schemas import (
    CurrencyStats,
    ItemStats,
    MerchantStats,
    MonthStats,
    StatsOutput,
    StatsPeriod,
)

PRAGUE = ZoneInfo("Europe/Prague")
TOP_ITEMS = 10
CENTS = Decimal("0.01")

router = APIRouter(prefix="/api/v1/overview", tags=["overview"])


def _money(amount: Decimal) -> str:
    return format(amount.quantize(CENTS, rounding=ROUND_HALF_UP), ".2f")


def _local_parts(
    dialect: str,
) -> tuple[ColumnElement[str], ColumnElement[int], ColumnElement[int]]:
    """Month key, Monday-based weekday and hour of the purchase in Prague wall time."""
    if dialect == "postgresql":
        local = func.timezone(PRAGUE.key, Receipt.purchased_at)
        return (
            func.to_char(local, "YYYY-MM", type_=String),
            cast(func.extract("isodow", local), Integer) - 1,
            cast(func.extract("hour", local), Integer),
        )
    local = func.cartalytix_prague_time(Receipt.purchased_at, type_=String)
    return (
        func.strftime("%Y-%m", local, type_=String),
        (cast(func.strftime("%w", local), Integer) + 6) % 7,
        cast(func.strftime("%H", local), Integer),
    )


def _month_key(value: datetime) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def _shift_months(value: datetime, months: int) -> datetime:
    index = value.year * 12 + value.month - 1 + months
    year, month = divmod(index, 12)
    day = min(value.day, monthrange(year, month + 1)[1])
    return value.replace(year=year, month=month + 1, day=day)


def _month_start(value: datetime) -> datetime:
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _range_filters(start: datetime | None, end: datetime | None) -> list[ColumnElement[bool]]:
    # SQLite stores naive UTC, so bounds are always bound as UTC.
    filters: list[ColumnElement[bool]] = []
    if start is not None:
        filters.append(Receipt.purchased_at >= start.astimezone(UTC))
    if end is not None:
        filters.append(Receipt.purchased_at < end.astimezone(UTC))
    return filters


def _top_items(
    filters: list[ColumnElement[bool]], order: ColumnElement[int] | ColumnElement[Decimal]
) -> Select[str, int, Decimal]:
    receipt_count = func.count(func.distinct(ReceiptItem.receipt_id))
    amount = func.sum(ReceiptItem.total_amount)
    return (
        select(ReceiptItem.name, receipt_count, amount)
        .join(Receipt, ReceiptItem.receipt_id == Receipt.id)
        .join(ProviderConnection, Receipt.connection_id == ProviderConnection.id)
        .where(*filters)
        .group_by(ReceiptItem.name)
        .order_by(order.desc(), ReceiptItem.name)
        .limit(TOP_ITEMS)
    )


def _item_stats(rows: Iterable[tuple[str, int, Decimal]]) -> list[ItemStats]:
    return [
        ItemStats(name=name, receipt_count=count, amount=_money(amount))
        for name, count, amount in rows
    ]


@router.get("/stats", response_model=StatsOutput)
def overview_stats(
    user: Owner,
    db: Db,
    period: StatsPeriod = StatsPeriod.LAST_12_MONTHS,
    month: Annotated[str | None, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")] = None,
) -> StatsOutput:
    now = datetime.now(PRAGUE)
    current_month = _month_start(now)
    period_start: datetime | None = None
    if period == StatsPeriod.LAST_12_MONTHS:
        period_start = _shift_months(current_month, -11)
    elif period == StatsPeriod.THIS_YEAR:
        period_start = current_month.replace(month=1)
    period_end = _shift_months(current_month, 1)

    start: datetime | None
    if month is not None:
        year, number = (int(part) for part in month.split("-"))
        start = datetime(year, number, 1, tzinfo=PRAGUE)
        end, shift = _shift_months(start, 1), 1
    else:
        start, end, shift = period_start, period_end, 12
    owner_filter = ProviderConnection.owner_id == user.id
    selection_filters = [owner_filter, *_range_filters(start, end)]
    month_key, weekday, hour = _local_parts(db.get_bind().dialect.name)
    amounts: dict[str, Decimal] = defaultdict(Decimal)
    counts: Counter[str] = Counter()
    by_merchant: dict[str, list[MerchantStats]] = defaultdict(list)
    merchant_amount = func.sum(Receipt.total_amount)
    for row_currency, merchant, count, amount in db.execute(
        select(Receipt.currency, Receipt.merchant, func.count(), merchant_amount)
        .join(ProviderConnection)
        .where(*selection_filters)
        .group_by(Receipt.currency, Receipt.merchant)
        .order_by(merchant_amount.desc(), Receipt.merchant)
    ):
        amounts[row_currency] += amount
        counts[row_currency] += count
        by_merchant[row_currency].append(
            MerchantStats(merchant=merchant, amount=_money(amount), receipt_count=count)
        )
    currency = max(sorted(counts), key=counts.__getitem__) if counts else None
    merchants = by_merchant[currency] if currency is not None else []

    previous: dict[str, tuple[int, Decimal]] = {}
    if start is not None:
        previous_end = _shift_months(min(end, now), -shift)
        previous = {
            row_currency: (count, amount)
            for row_currency, count, amount in db.execute(
                select(Receipt.currency, func.count(), func.sum(Receipt.total_amount))
                .join(ProviderConnection)
                .where(owner_filter, *_range_filters(_shift_months(start, -shift), previous_end))
                .group_by(Receipt.currency)
            )
        }
    discounts: dict[str, Decimal] = {
        row_currency: amount
        for row_currency, amount in db.execute(
            select(Receipt.currency, func.sum(Discount.amount))
            .select_from(Discount)
            .join(Receipt, Discount.receipt_id == Receipt.id)
            .join(ProviderConnection, Receipt.connection_id == ProviderConnection.id)
            .where(*selection_filters)
            .group_by(Receipt.currency)
        )
    }
    totals = [
        CurrencyStats(
            currency=code,
            amount=_money(amounts[code]),
            receipt_count=counts[code],
            average=_money(amounts[code] / counts[code]),
            previous_amount=None
            if start is None
            else _money(previous.get(code, (0, Decimal(0)))[1]),
            previous_receipt_count=None if start is None else previous.get(code, (0, 0))[0],
            discount_amount=_money(-discounts.get(code, Decimal(0))),
        )
        for code in sorted(counts, key=lambda code: (-counts[code], code))
    ]

    # Month buckets follow Prague wall time; the database groups, Python only fills gaps.
    month_merchants: dict[str, list[MerchantStats]] = defaultdict(list)
    month_amounts: dict[str, Decimal] = defaultdict(Decimal)
    month_counts: Counter[str] = Counter()
    if currency is not None:
        month_amount = func.sum(Receipt.total_amount)
        for key, merchant, count, amount in db.execute(
            select(month_key, Receipt.merchant, func.count(), month_amount)
            .join(ProviderConnection)
            .where(
                owner_filter,
                Receipt.currency == currency,
                *_range_filters(period_start, period_end),
            )
            .group_by(month_key, Receipt.merchant)
            .order_by(month_key, month_amount.desc(), Receipt.merchant)
        ):
            month_merchants[key].append(
                MerchantStats(merchant=merchant, amount=_money(amount), receipt_count=count)
            )
            month_amounts[key] += amount
            month_counts[key] += count
    first_month = period_start
    if first_month is None and month_counts:
        year, number = (int(part) for part in min(month_counts).split("-"))
        first_month = datetime(year, number, 1, tzinfo=PRAGUE)
    months: list[MonthStats] = []
    cursor = first_month
    while cursor is not None and cursor < period_end and currency is not None:
        key = _month_key(cursor)
        months.append(
            MonthStats(
                month=key,
                amount=_money(month_amounts[key]),
                receipt_count=month_counts[key],
                merchants=month_merchants[key],
            )
        )
        cursor = _shift_months(cursor, 1)

    weekly_hours = [[0] * 24 for _day in range(7)]
    for day, hour_of_day, count in db.execute(
        select(weekday, hour, func.count())
        .join(ProviderConnection)
        .where(*selection_filters)
        .group_by(weekday, hour)
    ):
        weekly_hours[int(day)][int(hour_of_day)] = count

    frequent_items: list[ItemStats] = []
    top_spend_items: list[ItemStats] = []
    if currency is not None:
        item_filters = [*selection_filters, Receipt.currency == currency]
        frequent_items = _item_stats(
            db.execute(_top_items(item_filters, func.count(func.distinct(ReceiptItem.receipt_id))))
        )
        top_spend_items = _item_stats(
            db.execute(_top_items(item_filters, func.sum(ReceiptItem.total_amount)))
        )

    return StatsOutput(
        period=period,
        month=month,
        currency=currency,
        totals=totals,
        months=months,
        merchants=merchants,
        frequent_items=frequent_items,
        top_spend_items=top_spend_items,
        weekly_hours=weekly_hours,
    )
