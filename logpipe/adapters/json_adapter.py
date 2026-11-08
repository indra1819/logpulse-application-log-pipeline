"""Adapter for one JSON object per log line."""
import json

from logpipe.errors import RejectError


def can_parse(line: str) -> bool:
    return line.lstrip().startswith("{")


def parse(line: str) -> dict:
    try:
        value = json.loads(line.strip())
    except json.JSONDecodeError:
        raise RejectError("malformed_json") from None
    if not isinstance(value, dict):
        raise RejectError("malformed_json")
    return value
