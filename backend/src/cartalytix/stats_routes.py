"""Owner-scoped spending statistics for the overview dashboard."""

from calendar import monthrange
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import ColumnElement, Integer, Select, String, cast, func, select

from cartalytix.dependencies import Db, Owner
from cartalytix.models import Discount, ProviderConnection, Receipt, ReceiptItem
from cartalytix.schemas import (
    STATS_BUCKET_PATTERN,
    BucketStats,
    CurrencyStats,
    ItemStats,
    MerchantStats,
    StatsGranularity,
    StatsOutput,
    StatsPeriod,
)

PRAGUE = ZoneInfo("Europe/Prague")
TOP_ITEMS = 50
CENTS = Decimal("0.01")
# Ranges up to this many days are charted per day, longer ones per month.
DAILY_MAX_DAYS = 92
# How far back the comparison window starts: (months, days).
Lookback = tuple[int, int]

router = APIRouter(prefix="/api/v1/overview", tags=["overview"])


def _money(amount: Decimal) -> str:
    return format(amount.quantize(CENTS, rounding=ROUND_HALF_UP), ".2f")


def _local_parts(
    dialect: str, granularity: StatsGranularity
) -> tuple[ColumnElement[str], ColumnElement[int], ColumnElement[int]]:
    """Bucket key, Monday-based weekday and hour of the purchase in Prague wall time."""
    day = granularity == StatsGranularity.DAY
    if dialect == "postgresql":
        local = func.timezone(PRAGUE.key, Receipt.purchased_at)
        return (
            func.to_char(local, "YYYY-MM-DD" if day else "YYYY-MM", type_=String),
            cast(func.extract("isodow", local), Integer) - 1,
            cast(func.extract("hour", local), Integer),
        )
    local = func.cartalytix_prague_time(Receipt.purchased_at, type_=String)
    return (
        func.strftime("%Y-%m-%d" if day else "%Y-%m", local, type_=String),
        (cast(func.strftime("%w", local), Integer) + 6) % 7,
        cast(func.strftime("%H", local), Integer),
    )


def _bucket_key(value: datetime, granularity: StatsGranularity) -> str:
    if granularity == StatsGranularity.DAY:
        return value.date().isoformat()
    return f"{value.year:04d}-{value.month:02d}"


def _shift_months(value: datetime, months: int) -> datetime:
    index = value.year * 12 + value.month - 1 + months
    year, month = divmod(index, 12)
    day = min(value.day, monthrange(year, month + 1)[1])
    return value.replace(year=year, month=month + 1, day=day)


def _back(value: datetime, lookback: Lookback) -> datetime:
    # Aware datetimes sharing one tzinfo add timedeltas in wall time, so midnight stays midnight.
    months, days = lookback
    return _shift_months(value, -months) - timedelta(days=days)


def _month_start(value: datetime) -> datetime:
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _day_start(value: date) -> datetime:
    return datetime.combine(value, time(), PRAGUE)


def _period_range(
    period: StatsPeriod, now: datetime, date_from: date | None, date_to: date | None
) -> tuple[datetime | None, datetime, Lookback | None]:
    """Half-open [start, end) of the period and how far back its comparison window lies."""
    tomorrow = _day_start(now.date() + timedelta(days=1))
    next_month = _shift_months(_month_start(now), 1)
    match period:
        case StatsPeriod.TODAY:
            return tomorrow - timedelta(days=1), tomorrow, (0, 1)
        case StatsPeriod.THIS_MONTH:
            return _month_start(now), tomorrow, (1, 0)
        case StatsPeriod.THIS_YEAR:
            return _month_start(now).replace(month=1), next_month, (12, 0)
        case StatsPeriod.LAST_30_DAYS:
            return tomorrow - timedelta(days=30), tomorrow, (0, 30)
        case StatsPeriod.LAST_90_DAYS:
            return tomorrow - timedelta(days=90), tomorrow, (0, 90)
        case StatsPeriod.LAST_12_MONTHS:
            return _shift_months(next_month, -12), next_month, (12, 0)
        case StatsPeriod.CUSTOM:
            if date_from is None or date_to is None or date_from > date_to:
                raise HTTPException(422, "invalid_date_range")
            end = _day_start(date_to + timedelta(days=1))
            return _day_start(date_from), end, (0, (date_to - date_from).days + 1)
        case StatsPeriod.ALL:
            return None, next_month, None


def _bucket_range(bucket: str) -> tuple[datetime, datetime, Lookback]:
    try:
        day = date.fromisoformat(bucket if len(bucket) > 7 else f"{bucket}-01")
    except ValueError as exc:
        raise HTTPException(422, "invalid_bucket") from exc
    start = _day_start(day)
    if len(bucket) > 7:
        return start, start + timedelta(days=1), (0, 1)
    return start, _shift_months(start, 1), (1, 0)


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
    bucket: Annotated[str | None, Query(pattern=STATS_BUCKET_PATTERN)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> StatsOutput:
    now = datetime.now(PRAGUE)
    period_start, period_end, lookback = _period_range(period, now, date_from, date_to)
    granularity = (
        StatsGranularity.DAY
        if period_start is not None and (period_end - period_start).days <= DAILY_MAX_DAYS
        else StatsGranularity.MONTH
    )

    start, end = period_start, period_end
    if bucket is not None:
        bucket_start, bucket_end, lookback = _bucket_range(bucket)
        start = bucket_start if period_start is None else max(bucket_start, period_start)
        end = min(bucket_end, period_end)
    owner_filter = ProviderConnection.owner_id == user.id
    selection_filters = [owner_filter, *_range_filters(start, end)]
    bucket_key, weekday, hour = _local_parts(db.get_bind().dialect.name, granularity)
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
    if start is not None and lookback is not None:
        previous_range = _back(start, lookback), _back(min(end, now), lookback)
        previous = {
            row_currency: (count, amount)
            for row_currency, count, amount in db.execute(
                select(Receipt.currency, func.count(), func.sum(Receipt.total_amount))
                .join(ProviderConnection)
                .where(owner_filter, *_range_filters(*previous_range))
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
            discount_amount=_money(discounts.get(code, Decimal(0))),
        )
        for code in sorted(counts, key=lambda code: (-counts[code], code))
    ]

    # Chart buckets follow Prague wall time; the database groups, Python only fills gaps.
    bucket_merchants: dict[str, list[MerchantStats]] = defaultdict(list)
    bucket_amounts: dict[str, Decimal] = defaultdict(Decimal)
    bucket_counts: Counter[str] = Counter()
    if currency is not None:
        bucket_amount = func.sum(Receipt.total_amount)
        for key, merchant, count, amount in db.execute(
            select(bucket_key, Receipt.merchant, func.count(), bucket_amount)
            .join(ProviderConnection)
            .where(
                owner_filter,
                Receipt.currency == currency,
                *_range_filters(period_start, period_end),
            )
            .group_by(bucket_key, Receipt.merchant)
            .order_by(bucket_key, bucket_amount.desc(), Receipt.merchant)
        ):
            bucket_merchants[key].append(
                MerchantStats(merchant=merchant, amount=_money(amount), receipt_count=count)
            )
            bucket_amounts[key] += amount
            bucket_counts[key] += count
    cursor = period_start
    if cursor is None and bucket_counts:
        cursor = _bucket_range(min(bucket_counts))[0]
    if cursor is not None and granularity == StatsGranularity.MONTH:
        cursor = _month_start(cursor)
    buckets: list[BucketStats] = []
    while cursor is not None and cursor < period_end and currency is not None:
        key = _bucket_key(cursor, granularity)
        buckets.append(
            BucketStats(
                key=key,
                amount=_money(bucket_amounts[key]),
                receipt_count=bucket_counts[key],
                merchants=bucket_merchants[key],
            )
        )
        cursor = (
            cursor + timedelta(days=1)
            if granularity == StatsGranularity.DAY
            else _shift_months(cursor, 1)
        )

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
        bucket=bucket,
        granularity=granularity,
        currency=currency,
        totals=totals,
        buckets=buckets,
        merchants=merchants,
        frequent_items=frequent_items,
        top_spend_items=top_spend_items,
        weekly_hours=weekly_hours,
    )
