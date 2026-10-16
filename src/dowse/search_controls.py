"""Visible search restrictions, with explicit overrides for query-inferred filters.

Removing an inferred chip disables that restriction, not the query's descriptive words.
Dates use photo capture time when available, otherwise the file's modification time.
"""

from datetime import date, timedelta

from . import dates, search_scope
from .rules import Kind
from .searcher import kind_filter


def resolve(query: str, kinds: frozenset[Kind], within: str | list[str] | None, ignored: list[str],
            date_range: dict | None) -> tuple[dates.Span | None, list[dict]]:
    if not isinstance(ignored, list) or any(item not in ("date", "kind") for item in ignored):
        raise ValueError("Unknown inferred filter")
    inferred = dates.parse(query)
    span = inferred if "date" not in ignored else None
    source = "query"
    if date_range is not None:
        if not isinstance(date_range, dict):
            raise ValueError("Date filter must contain From and To dates")
        first, last = date_range.get("from") or "", date_range.get("to") or ""
        try:
            start = date.fromisoformat(first) if first else None
            end = date.fromisoformat(last) if last else None
            if not (start or end) or (start and end and start > end):
                raise ValueError()
            span = dates.Span(dates._stamp(start) if start else float("-inf"),
                              dates._stamp(end + timedelta(days=1))
                              if end else float("inf"),
                              f"{first} – {last}" if start and end else f"From {first}" if start else f"Through {last}",
                              inferred.rest if inferred else query)
        except (ValueError, OverflowError, TypeError, OSError):
            raise ValueError("Choose valid dates, with From no later than To") from None
        source = "manual"
    effective_kinds = kinds or (kind_filter(inferred.rest if inferred else query) if "kind" not in ignored else set())
    filters = []
    for folder in search_scope.folders(within):
        filters.append({"id": "folder", "label": folder, "source": "manual"})
    if effective_kinds:
        filters.append({"id": "kind", "kinds": sorted(k.value for k in effective_kinds),
                        "source": "manual" if kinds else "query"})
    if span:
        filters.append({"id": "date", "label": span.label, "source": source,
                        **({"from": first, "to": last} if date_range is not None else {
                            "from": span.date_from, "to": span.date_to})})
    return span, filters
