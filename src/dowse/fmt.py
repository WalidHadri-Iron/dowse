"""How times read in progress lines and results."""


def clock(seconds: float) -> str:
    """1:05, 1:02:05."""
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"
