"""Search folder restrictions are a union, separate from the sources chosen for indexing."""

from .selection import norm, inside


def folders(within: str | list[str] | None) -> list[str]:
    """Accept legacy single-folder callers and deduplicate multi-folder choices as Windows does."""
    if within is None:
        return []
    paths = [within] if isinstance(within, str) else within
    if not isinstance(paths, list) or any(not isinstance(p, str) or not p.strip() for p in paths):
        raise ValueError("Choose valid folders for the search filter")
    unique = {}
    for path in paths:
        unique.setdefault(norm(path), path)
    return list(unique.values())


def contains(path: str, roots: list[str]) -> bool:
    """A file under any chosen folder qualifies; sibling prefixes never qualify."""
    target = norm(path)
    return not roots or any(inside(target, norm(root)) for root in roots)
