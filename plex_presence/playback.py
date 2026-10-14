"""Ignore completed timelines, including Plex's duration-minus-one end event."""
import math
import time


def activity_finished(activity, now=None):
    if not isinstance(activity, dict):
        return False
    try:
        end = float(activity.get("timestamps", {}).get("end", 0))
    except (TypeError, ValueError, AttributeError):
        return False
    if not math.isfinite(end) or end <= 0:
        return False
    if end >= 100_000_000_000:
        end /= 1000
    # Plex clients can leave a completed session at duration - 1 millisecond.
    return end <= (time.time() if now is None else now) + 0.25
