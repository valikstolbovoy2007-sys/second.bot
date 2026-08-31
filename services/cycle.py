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
