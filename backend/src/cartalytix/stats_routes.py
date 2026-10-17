"""Owner-scoped spending statistics for the overview dashboard."""

from calendar import monthrange
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from sqlalchemy import ColumnElement, Select, func, select

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


def _local(value: datetime) -> datetime:
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return aware.astimezone(PRAGUE)


def _month_key(value: datetime) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def _shift_months(value: datetime, months: int) -> datetime:
    index = value.year * 12 + value.month - 1 + months
    year, month = divmod(index, 12)
    day = min(value.day, monthrange(year, month + 1)[1])
    return value.replace(year=year, month=month + 1, day=day)


def _month_start(value: datetime) -> datetime:
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _in_range(value: datetime, start: datetime | None, end: datetime | None) -> bool:
    return (start is None or value >= start) and (end is None or value < end)


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
    rows_start = None if start is None or period_start is None else min(start, period_start)
    rows_end = max(end, period_end)

    owner_filter = ProviderConnection.owner_id == user.id
    rows = db.execute(
        select(Receipt.purchased_at, Receipt.merchant, Receipt.currency, Receipt.total_amount)
        .join(ProviderConnection)
        .where(owner_filter, *_range_filters(rows_start, rows_end))
    ).all()

    amounts: dict[str, Decimal] = defaultdict(Decimal)
    counts: Counter[str] = Counter()
    selected: list[tuple[datetime, str, str, Decimal]] = []
    for purchased_at, merchant, currency, total_amount in rows:
        local = _local(purchased_at)
        if _in_range(local, start, end):
            selected.append((local, merchant, currency, total_amount))
            amounts[currency] += total_amount
            counts[currency] += 1
    currency = max(sorted(counts), key=counts.__getitem__) if counts else None

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
    selection_filters = [owner_filter, *_range_filters(start, end)]
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

    month_amounts: dict[str, Decimal] = defaultdict(Decimal)
    month_counts: Counter[str] = Counter()
    month_merchant_amounts: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: defaultdict(Decimal)
    )
    month_merchant_counts: dict[str, Counter[str]] = defaultdict(Counter)
    first_month: datetime | None = period_start
    for purchased_at, merchant, row_currency, total_amount in rows:
        local = _local(purchased_at)
        if row_currency != currency or not _in_range(local, period_start, period_end):
            continue
        key = _month_key(local)
        month_amounts[key] += total_amount
        month_counts[key] += 1
        month_merchant_amounts[key][merchant] += total_amount
        month_merchant_counts[key][merchant] += 1
        if period_start is None and (first_month is None or local < first_month):
            first_month = _month_start(local)
    months: list[MonthStats] = []
    cursor = first_month
    while cursor is not None and cursor < period_end and currency is not None:
        key = _month_key(cursor)
        months.append(
            MonthStats(
                month=key,
                amount=_money(month_amounts[key]),
                receipt_count=month_counts[key],
                merchants=[
                    MerchantStats(
                        merchant=merchant,
                        amount=_money(amount),
                        receipt_count=month_merchant_counts[key][merchant],
                    )
                    for merchant, amount in sorted(
                        month_merchant_amounts[key].items(), key=lambda pair: (-pair[1], pair[0])
                    )
                ],
            )
        )
        cursor = _shift_months(cursor, 1)

    merchant_amounts: dict[str, Decimal] = defaultdict(Decimal)
    merchant_counts: Counter[str] = Counter()
    weekly_hours = [[0] * 24 for _day in range(7)]
    for local, merchant, row_currency, total_amount in selected:
        weekly_hours[local.weekday()][local.hour] += 1
        if row_currency == currency:
            merchant_amounts[merchant] += total_amount
            merchant_counts[merchant] += 1
    merchants = [
        MerchantStats(
            merchant=merchant,
            amount=_money(merchant_amounts[merchant]),
            receipt_count=merchant_counts[merchant],
        )
        for merchant in sorted(
            merchant_amounts, key=lambda merchant: (-merchant_amounts[merchant], merchant)
        )
    ]

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
