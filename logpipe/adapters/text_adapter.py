"""Adapter for timestamp LEVEL [service] key=value application text logs."""
import re

_TEXT_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
    r"\s+(?P<level>[A-Za-z]+)\s+\[(?P<service>[^\]]*)\]\s*(?P<rest>.*)$"
)
_KV_RE = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S*)')


def can_parse(line: str) -> bool:
    return bool(_TEXT_RE.match(line.strip()))


def parse(line: str) -> dict:
    match = _TEXT_RE.match(line.strip())
    if not match:
        raise ValueError("text adapter called for a non-text line")
    rest = match.group("rest")
    fields = {"timestamp": match.group("ts"), "level": match.group("level"),
              "service": match.group("service")}
    for key, raw_value in _KV_RE.findall(rest):
        value = raw_value
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1].replace('\\"', '"').replace('\\\\', '\\')
        fields[key.lower()] = value
    leftover = _KV_RE.sub("", rest).strip()
    if leftover and "msg" not in fields and "message" not in fields:
        fields["message"] = leftover
    return fields
