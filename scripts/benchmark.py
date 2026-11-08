"""Simple performance measurement: generate N rows, run the pipeline R times, report the median.

    python scripts/benchmark.py --rows 100000 --repeat 3
"""
import argparse
import statistics
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logpipe.generator import generate  # noqa: E402
from logpipe.pipeline import run_pipeline  # noqa: E402

SERVICES = Path(__file__).resolve().parent.parent / "data" / "reference" / "services.csv"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=100_000)
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    as_of = datetime(2026, 11, 7, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        labels = generate(args.rows, args.seed, root / "in", root / "labels", 4)
        ingest, summarize, total = [], [], []
        for i in range(args.repeat):
            res = run_pipeline(root / "in", root / f"db{i}.sqlite", root / f"out{i}", SERVICES, as_of=as_of, labels=labels)
            assert res.status == "success" and res.checks_passed, "benchmark run failed reconciliation"
            m = res.metrics
            ingest.append(m["timings_seconds"]["ingest"])
            summarize.append(m["timings_seconds"]["summarize_and_reconcile"])
            total.append(m["duration_seconds"])
        rows_read = res.metrics["rows_read"]
        med = statistics.median
        print(f"rows_read={rows_read} repeat={args.repeat}")
        print(f"ingest_seconds_median={med(ingest):.2f}  rows_per_second={rows_read / med(ingest):,.0f}")
        print(f"summarize_and_reconcile_seconds_median={med(summarize):.2f}")
        print(f"total_seconds_median={med(total):.2f}")


if __name__ == "__main__":
    main()
