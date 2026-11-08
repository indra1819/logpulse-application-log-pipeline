"""Timestamp and severity normalization. Pure functions, no I/O."""
import math
import re
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MIN_YEAR, MAX_YEAR = 1990, 2100  # anything outside is treated as an invalid timestamp

SEVERITY_MAP = {
    "TRACE": "DEBUG", "DEBUG": "DEBUG", "DBG": "DEBUG",
    "INFO": "INFO", "INFORMATION": "INFO", "NOTICE": "INFO", "AUDIT": "INFO",
    "WARN": "WARNING", "WARNING": "WARNING",
    "ERR": "ERROR", "ERROR": "ERROR", "SEVERE": "ERROR",
    "CRIT": "CRITICAL", "CRITICAL": "CRITICAL", "FATAL": "CRITICAL",
    "EMERG": "CRITICAL", "EMERGENCY": "CRITICAL", "ALERT": "CRITICAL",
}
ERROR_LEVELS = frozenset({"ERROR", "CRITICAL"})

_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})"
    r"(?:[.,](\d{1,9}))?\s*(Z|[+-]\d{2}(?::?\d{2})?)?$",
    re.IGNORECASE,
)
_CLF_RE = re.compile(r"^(\d{2})/([A-Za-z]{3})/(\d{4}):(\d{2}):(\d{2}):(\d{2}) ([+-])(\d{2})(\d{2})$")
_NUM_RE = re.compile(r"^\d+(?:\.\d+)?$")
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def normalize_severity(value) -> str:
    """Map any known spelling to DEBUG/INFO/WARNING/ERROR/CRITICAL, else ValueError."""
    if not isinstance(value, str):
        raise ValueError(f"severity must be a string, got {type(value).__name__}")
    try:
        return SEVERITY_MAP[value.strip().upper()]
    except KeyError:
        raise ValueError(f"unknown severity: {value!r}") from None


def _offset(sign: str, hours: int, minutes: int) -> timezone:
    if minutes > 59:
        raise ValueError("invalid UTC offset")
    delta = timedelta(hours=hours, minutes=minutes)
    return timezone(-delta if sign == "-" else delta)  # raises ValueError if >= 24h


def _from_epoch(x: float) -> datetime:
    if not math.isfinite(x) or x < 0:
        raise ValueError("invalid epoch value")
    millis = round(x) if x >= 1e11 else round(x * 1000)  # >= 1e11 means milliseconds
    try:
        return EPOCH + timedelta(milliseconds=millis)
    except OverflowError:
        raise ValueError("epoch out of range") from None


def _check_range(dt: datetime) -> datetime:
    if not (MIN_YEAR <= dt.year <= MAX_YEAR):
        raise ValueError(f"timestamp year {dt.year} outside {MIN_YEAR}-{MAX_YEAR}")
    return dt


def normalize_timestamp(value) -> datetime:
    """Parse many timestamp spellings into an aware UTC datetime (millisecond precision).

    Accepted: ISO-8601 (T or space separator, optional fraction with . or ,, optional
    Z/offset), epoch seconds, epoch milliseconds (numbers or numeric strings), and
    Apache-style `01/Nov/2026:10:00:00 +0530`. Timestamps without an offset are
    assumed to already be UTC. Anything else raises ValueError.
    """
    if value is None or isinstance(value, bool):
        raise ValueError("timestamp missing or boolean")
    if isinstance(value, (int, float)):
        return _check_range(_from_epoch(float(value)))
    if not isinstance(value, str):
        raise ValueError(f"unsupported timestamp type {type(value).__name__}")

    s = value.strip()
    if _NUM_RE.match(s):
        return _check_range(_from_epoch(float(s)))

    m = _CLF_RE.match(s)
    if m:
        day, mon, year, hh, mi, ss, sign, oh, om = m.groups()
        month = _MONTHS.get(mon.lower())
        if month is None:
            raise ValueError(f"bad month {mon!r}")
        dt = datetime(int(year), month, int(day), int(hh), int(mi), int(ss),
                      tzinfo=_offset(sign, int(oh), int(om)))
        return _check_range(dt.astimezone(UTC))

    m = _ISO_RE.match(s)
    if not m:
        raise ValueError(f"unrecognised timestamp format: {value!r}")
    y, mo, d, hh, mi, ss, frac, tz = m.groups()
    micro = int((frac or "0")[:3].ljust(3, "0")) * 1000  # truncate to milliseconds
    if tz is None:
        tzinfo = UTC
    elif tz.upper() == "Z":
        tzinfo = UTC
    else:
        digits = tz[1:].replace(":", "")
        tzinfo = _offset(tz[0], int(digits[:2]), int(digits[2:4] or 0))
    dt = datetime(int(y), int(mo), int(d), int(hh), int(mi), int(ss), micro, tzinfo=tzinfo)
    return _check_range(dt.astimezone(UTC))


def to_iso_utc(dt: datetime) -> str:
    """ISO-8601 UTC with millisecond precision, e.g. 2026-11-01T10:00:00.123Z."""
    dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{dt.microsecond // 1000:03d}Z"


def hour_bucket(dt: datetime) -> str:
    """Start of the UTC hour containing dt, e.g. 2026-11-01T10:00:00Z."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:00:00Z")
