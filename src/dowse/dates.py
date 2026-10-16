"""When a query says when: "last summer", "from July 2025", "yesterday", "3 weeks ago", "taken in 2024".

The model knows what's in a file, not when it was made, so a date in the query is a filter (on when a photo was
taken, or else when the file was last changed), and the words that said it are taken out of what the model is asked.
Only what's said plainly is read: a bare year ("tax return 2019") or a bare month name ("May") stays a word, since
it's as often what a file is about as when it's from. Seasons are the northern hemisphere's.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

MONTHS = {name: number for number, names in enumerate(
    [("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",), ("june", "jun"),
     ("july", "jul"), ("august", "aug"), ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"),
     ("december", "dec")], 1) for name in names}
SEASONS = {"spring": 3, "summer": 6, "autumn": 9, "fall": 9, "winter": 12}   # the month each starts
UNITS = {"day": "day", "days": "day", "week": "week", "weeks": "week", "month": "month", "months": "month",
         "year": "year", "years": "year"}
NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
           "nine": 9, "ten": 10, "a couple of": 2, "a few": 3}

_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
_SEASON = "|".join(SEASONS)
_YEAR = r"(?:19[7-9]\d|20\d\d)"
_LEAD = r"(?:(?:taken|made|written|shot|saved|modified|edited|changed|sent|received|from|in|during|on|since)\s+)"
_COUNT = r"(\d+|" + "|".join(sorted(NUMBERS, key=len, reverse=True)) + ")"

_PATTERNS = [   # (pattern, kind) in the order tried; each match is taken out of the query
    (rf"\b{_LEAD}*(?:the\s+)?(last|this|past|previous)\s+({_SEASON})\b", "season-relative"),
    (rf"\b{_LEAD}*(?:the\s+)?({_SEASON})\s+(?:of\s+)?({_YEAR})\b", "season-year"),
    (rf"\b{_LEAD}*(?:the\s+)?(last|this|past|previous)\s+(week|month|year)\b", "period-relative"),
    (rf"\b{_LEAD}*{_COUNT}\s+(days?|weeks?|months?|years?)\s+ago\b", "ago"),
    (rf"\b{_LEAD}*(today|yesterday)\b", "day"),
    (rf"\b{_LEAD}*({_MONTH})\.?\s+({_YEAR})\b", "month-year"),
    (rf"\b{_LEAD}+(?:the\s+month\s+of\s+)?({_MONTH})\b(?!\s+\d)", "month"),
    (rf"\b{_LEAD}+(?:the\s+year\s+)?({_YEAR})\b", "year"),
]


@dataclass(frozen=True)
class Span:
    start: float   # timestamps, local time: [start, end)
    end: float
    label: str     # "summer 2026", "July 2025", "yesterday"
    rest: str      # the query without the words that said when
    date_from: str | None = None
    date_to: str | None = None


def parse(text: str, now: float | None = None) -> Span | None:
    """The first span of time the query names plainly, or None."""
    today = datetime.fromtimestamp(now).date() if now is not None else date.today()
    folded = text.lower()
    for pattern, kind in _PATTERNS:
        match = re.search(pattern, folded)
        if not match:
            continue
        found = _span(kind, match.groups(), today)
        if found is None:
            continue
        first, last, label = found
        if match.group(0).split()[0] == "since":   # from then until now
            last, label = today, f"since {label}"
        rest = " ".join((text[:match.start()] + " " + text[match.end():]).split())
        return Span(_stamp(first), _stamp(last + timedelta(days=1)), label, rest, first.isoformat(), last.isoformat())
    return None


def for_meaning(text: str, now: float | None = None) -> str:
    """What the model should be asked: the query without its date ("sunflowers from last summer" → "sunflowers")."""
    span = parse(text, now)
    return span.rest if span and span.rest.strip() else text


def taken(value: str | None) -> float | None:
    """A photo's EXIF time, shared by inferred and manual date filters."""
    try:
        return datetime.strptime(value[:19], "%Y:%m:%d %H:%M:%S").timestamp() if value else None
    except (ValueError, TypeError):
        return None


def _stamp(day: date) -> float:
    local = datetime(day.year, day.month, day.day)
    try:
        return local.timestamp()
    except OSError:
        # Windows' CRT rejects pre-epoch local timestamps (even 1970-01-01 in Paris).
        # Convert with the same system timezone, then use an aware UTC datetime instead of mktime.
        import ctypes
        import os
        if os.name != "nt":
            raise
        class SystemTime(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ushort) for name in
                        ("year", "month", "weekday", "day", "hour", "minute", "second", "millisecond")]
        source, utc = SystemTime(year=day.year, month=day.month, day=day.day), SystemTime()
        if not ctypes.windll.kernel32.TzSpecificLocalTimeToSystemTime(None, ctypes.byref(source), ctypes.byref(utc)):
            raise ValueError("This date is outside the system's supported range") from None
        return datetime(utc.year, utc.month, utc.day, utc.hour, utc.minute, utc.second, tzinfo=timezone.utc).timestamp()


def _month_span(year: int, month: int) -> tuple[date, date]:
    first = date(year, month, 1)
    following = date(year + (month == 12), month % 12 + 1, 1)
    return first, following - timedelta(days=1)


def _season_span(name: str, year: int) -> tuple[date, date]:
    """`year`'s season; winter is the one that ends in `year` (December before it to February)."""
    start = SEASONS[name]
    if start == 12:
        return date(year - 1, 12, 1), _month_span(year, 2)[1]
    return date(year, start, 1), _month_span(year, start + 2)[1]


def _span(kind: str, groups: tuple, today: date) -> tuple[date, date, str] | None:
    if kind == "season-relative":
        which, name = groups
        year = today.year + (1 if SEASONS[name] == 12 and today.month == 12 else 0)
        first, last = _season_span(name, year)
        if which in ("last", "past", "previous"):   # the latest one that's over
            while last >= today:
                year -= 1
                first, last = _season_span(name, year)
        elif first > today:   # "this summer" in March: the one coming, so far nothing
            year -= 1
            first, last = _season_span(name, year)
        label = f"winter {first.year}–{last.year}" if SEASONS[name] == 12 else f"{name} {first.year}"
        return first, last, label
    if kind == "season-year":
        name, year = groups
        first, last = _season_span(name, int(year))
        return first, last, f"{name} {year}"
    if kind == "period-relative":
        which, unit = groups
        back = which != "this"
        if unit == "week":
            monday = today - timedelta(days=today.weekday()) - timedelta(weeks=back)
            return monday, monday + timedelta(days=6), f"{which} week"
        if unit == "month":
            year, month = (today.year, today.month) if not back else \
                (today.year - (today.month == 1), (today.month - 2) % 12 + 1)
            first, last = _month_span(year, month)
            return first, last, first.strftime("%B %Y")
        year = today.year - back
        return date(year, 1, 1), date(year, 12, 31), str(year)
    if kind == "ago":
        count, unit = groups
        n = int(count) if count.isdigit() else NUMBERS[count]
        unit = UNITS[unit]
        if unit == "day":
            day = today - timedelta(days=n)
            return day - timedelta(days=1), day + timedelta(days=1), f"{n} day{'s' * (n != 1)} ago"
        if unit == "week":
            monday = today - timedelta(days=today.weekday()) - timedelta(weeks=n)
            return monday, monday + timedelta(days=6), f"{n} week{'s' * (n != 1)} ago"
        if unit == "month":
            index = today.year * 12 + today.month - 1 - n
            first, last = _month_span(index // 12, index % 12 + 1)
            return first, last, first.strftime("%B %Y")
        return date(today.year - n, 1, 1), date(today.year - n, 12, 31), str(today.year - n)
    if kind == "day":
        day = today - timedelta(days=groups[0] == "yesterday")
        return day, day, groups[0]
    if kind == "month-year":
        month, year = MONTHS[groups[0]], int(groups[1])
        first, last = _month_span(year, month)
        return first, last, first.strftime("%B %Y")
    if kind == "month":
        month = MONTHS[groups[0]]
        year = today.year if month <= today.month else today.year - 1   # the latest one not in the future
        first, last = _month_span(year, month)
        return first, last, first.strftime("%B %Y")
    if kind == "year":
        year = int(groups[0])
        if year > today.year:
            return None
        return date(year, 1, 1), date(year, 12, 31), str(year)
    return None
