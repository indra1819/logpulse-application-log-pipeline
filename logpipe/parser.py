"""Format dispatcher for supported application log layouts.

Each format lives in its own adapter so parsing rules can evolve and be tested
independently while the rest of the pipeline consumes one stable interface.
"""
from logpipe.adapters import clf_adapter, json_adapter, openstack_adapter, text_adapter
from logpipe.errors import RejectError


def parse_line(line: str, clf_service=None) -> dict:
    stripped = line.strip()
    if json_adapter.can_parse(stripped):
        return json_adapter.parse(stripped)
    if text_adapter.can_parse(stripped):
        return text_adapter.parse(stripped)
    if openstack_adapter.can_parse(stripped):
        return openstack_adapter.parse(stripped)
    if clf_adapter.can_parse(stripped):
        return clf_adapter.parse(stripped, clf_service)
    raise RejectError("malformed_line")
