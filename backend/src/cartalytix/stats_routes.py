"""Owner-scoped spending statistics for the overview dashboard."""

from calendar import monthrange
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import (
    ColumnElement,
    Integer,
    Select,
    String,
    and_,
    case,
    cast,
    false,
    func,
    select,
    true,
)
from sqlalchemy.orm import Session
from sqlalchemy.sql.selectable import CTE

from cartalytix.accounting import accounting
from cartalytix.dependencies import Db, Owner
from cartalytix.models import (
    Category,
    CategoryMembership,
    CategoryProduct,
    ProviderConnection,
    Receipt,
    ReceiptItem,
)
from cartalytix.schemas import (
    STATS_BUCKET_PATTERN,
    BucketStats,
    CategoryStats,
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


def _top_items(filters: list[ColumnElement[bool]], item_amounts: CTE) -> Select[str, int, Decimal]:
    receipt_count = func.count(func.distinct(ReceiptItem.receipt_id))
    amount = func.sum(item_amounts.c.included_amount)
    return (
        select(ReceiptItem.name, receipt_count, amount)
        .join(Receipt, ReceiptItem.receipt_id == Receipt.id)
        .join(ProviderConnection, Receipt.connection_id == ProviderConnection.id)
        .join(item_amounts, ReceiptItem.id == item_amounts.c.id)
        .where(*filters, item_amounts.c.is_included)
        .group_by(ReceiptItem.name)
    )


def _category_stats(
    db: Session, filters: list[ColumnElement[bool]], item_amounts: CTE
) -> list[CategoryStats]:
    """Purchased lines and spending per category; empty until something is categorized."""
    amount = func.sum(item_amounts.c.included_amount)
    rows = db.execute(
        select(Category.id, Category.name, func.count(ReceiptItem.id), amount)
        .select_from(ReceiptItem)
        .join(Receipt, ReceiptItem.receipt_id == Receipt.id)
        .join(ProviderConnection, Receipt.connection_id == ProviderConnection.id)
        .join(item_amounts, ReceiptItem.id == item_amounts.c.id)
        .outerjoin(
            CategoryProduct,
            and_(
                CategoryProduct.connection_id == Receipt.connection_id,
                CategoryProduct.name == ReceiptItem.name,
                CategoryProduct.currency == Receipt.currency,
            ),
        )
        .outerjoin(CategoryMembership, CategoryMembership.product_id == CategoryProduct.id)
        .outerjoin(Category, Category.id == CategoryMembership.category_id)
        .where(*filters, item_amounts.c.is_included)
        .group_by(Category.id, Category.name)
        .order_by(amount.desc(), Category.name)
    ).all()
    if all(category_id is None for category_id, *_rest in rows):
        return []
    return [
        CategoryStats(
            category_id=category_id, name=name, purchase_count=count, amount=_money(total)
        )
        for category_id, name, count, total in rows
    ]


def _merchants(rows: Iterable[tuple[str, tuple[int, Decimal]]]) -> list[MerchantStats]:
    ordered = sorted(rows, key=lambda row: (-row[1][1], row[0]))
    return [
        MerchantStats(merchant=merchant, amount=_money(amount), receipt_count=count)
        for merchant, (count, amount) in ordered
    ]


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
    accounting_start = period_start
    if start is not None and lookback is not None and accounting_start is not None:
        accounting_start = min(accounting_start, _back(start, lookback))
    item_amounts, receipt_amounts = accounting(
        user.id,
        select(Receipt.id).where(*_range_filters(accounting_start, period_end)),
    )
    bucket_key, weekday, hour = _local_parts(db.get_bind().dialect.name, granularity)
    previous_range = (
        (_back(start, lookback), _back(min(end, now), lookback))
        if start is not None and lookback is not None
        else None
    )
    # One pass over the allocated receipts feeds every receipt-level figure; each statement would
    # otherwise recompute the whole allocation.
    in_selection = and_(true(), *_range_filters(start, end))
    in_period = and_(true(), *_range_filters(period_start, period_end))
    in_previous = and_(true(), *_range_filters(*previous_range)) if previous_range else false()
    amount = receipt_amounts.c.included_amount
    groups = db.execute(
        select(
            Receipt.currency,
            Receipt.merchant,
            bucket_key,
            weekday,
            hour,
            func.sum(case((in_selection, 1), else_=0)),
            func.sum(case((in_selection, amount), else_=0)),
            func.sum(case((in_selection, receipt_amounts.c.included_discount_amount), else_=0)),
            func.sum(case((in_period, 1), else_=0)),
            func.sum(case((in_period, amount), else_=0)),
            func.sum(case((in_previous, 1), else_=0)),
            func.sum(case((in_previous, amount), else_=0)),
        )
        .join(ProviderConnection)
        .join(receipt_amounts, Receipt.id == receipt_amounts.c.receipt_id)
        .where(owner_filter, receipt_amounts.c.is_included)
        .group_by(Receipt.currency, Receipt.merchant, bucket_key, weekday, hour)
    ).all()
    amounts: dict[str, Decimal] = defaultdict(Decimal)
    counts: Counter[str] = Counter()
    discounts: dict[str, Decimal] = defaultdict(Decimal)
    merchant_totals: dict[str, dict[str, tuple[int, Decimal]]] = defaultdict(dict)
    previous: dict[str, tuple[int, Decimal]] = {}
    weekly_hours = [[0] * 24 for _day in range(7)]
    for (
        row_currency,
        merchant,
        _key,
        day,
        hour_of_day,
        count,
        total,
        discount,
        _period_count,
        _period_total,
        previous_count,
        previous_total,
    ) in groups:
        if count:
            amounts[row_currency] += Decimal(total)
            counts[row_currency] += count
            discounts[row_currency] += Decimal(discount)
            merchant_count, merchant_total = merchant_totals[row_currency].get(
                merchant, (0, Decimal(0))
            )
            merchant_totals[row_currency][merchant] = (
                merchant_count + count,
                merchant_total + Decimal(total),
            )
            weekly_hours[int(day)][int(hour_of_day)] += count
        if previous_count:
            known_count, known_total = previous.get(row_currency, (0, Decimal(0)))
            previous[row_currency] = (
                known_count + previous_count,
                known_total + Decimal(previous_total),
            )
    currency = max(sorted(counts), key=counts.__getitem__) if counts else None
    merchants = _merchants(merchant_totals[currency].items()) if currency is not None else []
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
    bucket_totals: dict[str, dict[str, tuple[int, Decimal]]] = defaultdict(dict)
    bucket_amounts: dict[str, Decimal] = defaultdict(Decimal)
    bucket_counts: Counter[str] = Counter()
    for row in groups:
        row_currency, merchant, key, count, total = (*row[:3], row[8], row[9])
        if row_currency != currency or not count:
            continue
        merchant_count, merchant_total = bucket_totals[key].get(merchant, (0, Decimal(0)))
        bucket_totals[key][merchant] = (merchant_count + count, merchant_total + Decimal(total))
        bucket_amounts[key] += Decimal(total)
        bucket_counts[key] += count
    bucket_merchants = {key: _merchants(rows.items()) for key, rows in bucket_totals.items()}
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
                merchants=bucket_merchants.get(key, []),
            )
        )
        cursor = (
            cursor + timedelta(days=1)
            if granularity == StatsGranularity.DAY
            else _shift_months(cursor, 1)
        )

    frequent_items: list[ItemStats] = []
    top_spend_items: list[ItemStats] = []
    categories: list[CategoryStats] = []
    if currency is not None:
        item_filters = [owner_filter, *_range_filters(start, end), Receipt.currency == currency]
        categories = _category_stats(db, item_filters, item_amounts)
        # Both rankings come from one pass over the allocation, ranked here.
        products = [
            (name, count, amount)
            for name, count, amount in db.execute(_top_items(item_filters, item_amounts))
        ]
        frequent_items = _item_stats(
            sorted(products, key=lambda row: (-row[1], row[0]))[:TOP_ITEMS]
        )
        top_spend_items = _item_stats(
            sorted(products, key=lambda row: (-row[2], row[0]))[:TOP_ITEMS]
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
        categories=categories,
        weekly_hours=weekly_hours,
    )
