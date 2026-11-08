"""Adapter for Apache Common Log Format access logs."""
import re

_CLF_RE = re.compile(
    r'^(?P<host>\S+)\s+\S+\s+\S+\s+\[(?P<ts>[^\]]+)\]\s+"(?P<request>.*)"\s+'
    r'(?P<status>\d{3}|-)\s+(?:\d+|-)\s*$'
)


def can_parse(line: str) -> bool:
    return bool(_CLF_RE.match(line.strip()))


def parse(line: str, clf_service=None) -> dict:
    match = _CLF_RE.match(line.strip())
    if not match:
        raise ValueError("CLF adapter called for a non-CLF line")
    request = match.group("request").strip()
    parts = request.split()
    status = match.group("status")
    if status.isdigit():
        level = "ERROR" if int(status) >= 500 else "WARNING" if int(status) >= 400 else "INFO"
    else:
        level = "INFO"
    fields = {"timestamp": match.group("ts"), "level": level, "client_host": match.group("host"),
              "status": status, "message": request or None,
              "endpoint": parts[1] if len(parts) >= 2 else (request or None)}
    if clf_service:
        fields["service"] = clf_service
    return fields
