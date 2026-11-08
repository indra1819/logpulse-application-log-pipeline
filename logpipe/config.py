from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Config:
    """Tunable pipeline parameters. Every field has a documented default."""

    # Latency above this is treated as a data error, not a slow request (1 hour).
    max_latency_ms: int = 3_600_000
    # Events timestamped later than `as_of + skew` are rejected as future-dated.
    max_future_skew_hours: int = 24
    # A line older than (max timestamp seen so far in the file - tolerance)
    # is flagged is_out_of_order = 1. It is still loaded.
    out_of_order_tolerance_s: int = 300
    min_http_status: int = 100
    max_http_status: int = 599
    # If more than this share of a file's rows are rejected, log a schema-drift warning.
    schema_drift_reject_ratio: float = 0.5
    # 0 = one transaction per file (default, atomic). N > 0 commits every N rows.
    # Only used by scripts/benchmark.py as the "naive" baseline.
    commit_every: int = 0
    input_extensions: tuple = (".log", ".jsonl", ".json", ".txt", ".gz")
    # Service name given to Common Log Format lines (Apache/NASA access logs have no service field).
    # None = CLF lines are rejected as missing_required_field:service.
    clf_service: Optional[str] = None
