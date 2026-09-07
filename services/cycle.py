from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum


class EventType(StrEnum):
    ARRIVAL = "arrival"
    MAX_DISCOUNT = "max_discount"
    MIDDLE = "middle"


EVENT_LABELS_RU: dict[EventType, str] = {
    EventType.ARRIVAL: "День завоза",
    EventType.MAX_DISCOUNT: "День максимальной скидки",
    EventType.MIDDLE: "Середина цикла",
}


@dataclass(frozen=True)
class CycleInfo:
    cycle_length: int
    anchor_date: date

    def __post_init__(self) -> None:
        if self.cycle_length < 2:
            raise ValueError("cycle_length must be >= 2")


def _target_day(info: CycleInfo, event: EventType) -> int:
    match event:
        case EventType.ARRIVAL:
            return 0
        case EventType.MAX_DISCOUNT:
            return info.cycle_length - 1
        case EventType.MIDDLE:
            return info.cycle_length // 2


def day_in_cycle(d: date, info: CycleInfo) -> int:
    return (d - info.anchor_date).days % info.cycle_length


def events_on(d: date, info: CycleInfo) -> set[EventType]:
    day = day_in_cycle(d, info)
    return {e for e in EventType if day == _target_day(info, e)}


def next_event_date(
    from_date: date,
    info: CycleInfo,
    event: EventType,
    *,
    allow_today: bool = True,
) -> date:
    target = _target_day(info, event)
    current = day_in_cycle(from_date, info)
    diff = (target - current) % info.cycle_length
    if diff == 0 and not allow_today:
        diff = info.cycle_length
    return from_date + timedelta(days=diff)


def days_until(from_date: date, info: CycleInfo, event: EventType) -> int:
    return (next_event_date(from_date, info, event) - from_date).days


_WEEKDAY_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def first_weekday_of_month(anchor_month: date, weekday: int) -> date:
    """First occurrence of `weekday` (0=Пн..6=Вс) in `anchor_month`'s month."""
    first = anchor_month.replace(day=1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset)


def monthly_weekday_cycle(today: date, weekday: int) -> CycleInfo:
    """Recomputes (anchor, cycle_length) fresh from `today` every call.

    Shops that deliver on "the first Thursday of the month" don't fit a
    fixed-length cycle — months vary 28-31 days. Rather than storing a
    static cycle_length that drifts out of sync, we derive it live: anchor
    is the most recent real occurrence of `weekday` on-or-before `today`,
    and cycle_length is the exact number of days to the *next* real
    occurrence. Since this is recomputed from `date.today()` on every
    call, it never goes stale — no background job needed to "advance" it.
    """
    this_month = first_weekday_of_month(today, weekday)
    if this_month <= today:
        anchor = this_month
    else:
        prev_month_end = today.replace(day=1) - timedelta(days=1)
        anchor = first_weekday_of_month(prev_month_end, weekday)
    next_month_start = (anchor.replace(day=1) + timedelta(days=32)).replace(day=1)
    next_occurrence = first_weekday_of_month(next_month_start, weekday)
    return CycleInfo(cycle_length=(next_occurrence - anchor).days, anchor_date=anchor)


def resolve_cycle_info(
    cycle_length: int | None,
    anchor_date: date | None,
    monthly_weekday: int | None,
    today: date,
) -> CycleInfo | None:
    """Single place that decides which recurrence model a shop uses.

    `monthly_weekday` (set via the admin's "first weekday of month" picker)
    always wins over a manually-set fixed cycle — see monthly_weekday_cycle.
    """
    if monthly_weekday is not None:
        return monthly_weekday_cycle(today, monthly_weekday)
    if cycle_length and anchor_date:
        return CycleInfo(cycle_length, anchor_date)
    return None


def humanize_days(n: int) -> str:
    if n == 0:
        return "сегодня"
    if n == 1:
        return "завтра"
    if n == 2:
        return "послезавтра"
    last_two = n % 100
    last = n % 10
    if 11 <= last_two <= 14:
        word = "дней"
    elif last == 1:
        word = "день"
    elif 2 <= last <= 4:
        word = "дня"
    else:
        word = "дней"
    return f"через {n} {word}"
