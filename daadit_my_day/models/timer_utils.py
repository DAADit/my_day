"""Pure timer accounting, shared by display and booking."""


def timer_elapsed(started_at, beat_at, now, beat_limit):
    """Count only up to the last heartbeat after a session has expired."""
    if not started_at:
        return 0
    beat = beat_at or started_at
    end = now if now - beat <= beat_limit else beat
    return max(0, int((end - started_at).total_seconds()))


def timer_expired(started_at, beat_at, now, beat_limit):
    return bool(started_at and now - (beat_at or started_at) > beat_limit)
