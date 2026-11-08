"""Adapter for OpenStack/oslo service logs (including Loghub filename prefixes)."""
import re

_OS_RE = re.compile(
    r"^(?:(?P<prefix>\S*\.log\S*)\s+)?(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[.,]\d+)\s+(?P<pid>\d+)\s+"
    r"(?P<level>[A-Za-z]+)\s+(?P<component>[\w.\-]+)\s*(?P<rest>.*)$"
)
_OS_CTX_RE = re.compile(r"^\[(?P<ctx>[^\]]*)\]\s*(?P<tail>.*)$")
_OS_REQ_RE = re.compile(r"(req-[0-9a-fA-F-]+)")
_OS_HTTP_RE = re.compile(
    r'^(?:(?P<ip>[^\s"]+)\s+)?"(?P<method>[A-Z]+)\s+(?P<path>\S+)[^"]*"\s+status:\s*(?P<status>\d{3})'
    r"\s+len:\s*(?P<len>\d+)(?:\s+time:\s*(?P<time>\d+(?:\.\d+)?))?\s*$"
)


def can_parse(line: str) -> bool:
    return bool(_OS_RE.match(line.strip()))


def parse(line: str) -> dict:
    match = _OS_RE.match(line.strip())
    if not match:
        raise ValueError("OpenStack adapter called for a non-OpenStack line")
    component = match.group("component")
    prefix = match.group("prefix")
    service = prefix.split(".log")[0].lower() if prefix else component.split(".")[0].lower()
    fields = {"timestamp": match.group("ts"), "level": match.group("level"),
              "service": service, "component": component}
    rest = match.group("rest").strip()
    context = _OS_CTX_RE.match(rest)
    tail = rest
    if context:
        tail = context.group("tail").strip()
        request_id = _OS_REQ_RE.search(context.group("ctx"))
        if request_id:
            fields["request_id"] = request_id.group(1)
    http = _OS_HTTP_RE.match(tail)
    if http:
        fields["endpoint"] = http.group("path")
        fields["status"] = http.group("status")
        fields["message"] = f'{http.group("method")} {http.group("path")}'
        if http.group("ip"):
            fields["client_host"] = http.group("ip")
        if http.group("time"):
            fields["latency_s"] = http.group("time")
    elif tail:
        fields["message"] = tail
    return fields
