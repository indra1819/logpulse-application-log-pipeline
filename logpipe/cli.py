import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from logpipe.config import Config
from logpipe.generator import generate
from logpipe.pipeline import run_pipeline
from logpipe.normalize import normalize_timestamp


class _RunIdDefault(logging.Filter):
    def filter(self, record):
        if not hasattr(record, "run_id"):
            record.run_id = "-"
        return True


def _close_logging():
    """Flush, detach, and close CLI-owned handlers (important on Windows)."""
    root = logging.getLogger("logpipe")
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        try:
            handler.flush()
        finally:
            handler.close()


def _setup_logging(out_dir: Path, verbose: bool):
    fmt = logging.Formatter("%(asctime)s %(levelname)s run_id=%(run_id)s %(message)s")
    # A CLI may be invoked more than once in-process (for example, by tests).
    # Detach and close old FileHandlers before opening another file.
    _close_logging()
    handlers = [logging.StreamHandler(sys.stderr)]
    out_dir.mkdir(parents=True, exist_ok=True)
    handlers.append(logging.FileHandler(out_dir / "pipeline.log", encoding="utf-8"))
    root = logging.getLogger("logpipe")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(_RunIdDefault())
        root.addHandler(h)


def build_parser():
    p = argparse.ArgumentParser(prog="logpipe", description="Application log pipeline")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="ingest logs, build summaries, reconcile")
    r.add_argument("--input", required=True, help="log file or directory")
    r.add_argument("--db", default="out/logpipe.db", help="SQLite target (default out/logpipe.db)")
    r.add_argument("--out", default="out", help="output directory for reports (default out)")
    r.add_argument("--services", default="data/reference/services.csv", help="reference list of valid services")
    r.add_argument("--run-id", help="explicit run id (default: generated)")
    r.add_argument("--as-of", help="reference 'now' for the future-timestamp rule, ISO-8601 (default: current time)")
    r.add_argument("--force", action="store_true", help="re-process files even if their content was already ingested")
    r.add_argument("--clf-service", help="service name for Common Log Format (Apache/NASA access log) lines, e.g. nasa_web; must exist in the services file")
    r.add_argument("--labels", help="expected_labels.json to compare against (generated datasets only)")
    r.add_argument("-v", "--verbose", action="store_true")

    q = sub.add_parser("query", help="run read-only SQL against the target database")
    q.add_argument("--db", default="out/logpipe.db")
    src = q.add_mutually_exclusive_group(required=True)
    src.add_argument("--sql", help="one SQL statement")
    src.add_argument("--file", help="file with SQL statements ending in ';'")

    g = sub.add_parser("generate", help="create a reproducible synthetic dataset")
    g.add_argument("--rows", type=int, default=300, help="number of distinct valid events")
    g.add_argument("--seed", type=int, default=42)
    g.add_argument("--files", type=int, default=3)
    g.add_argument("--out-dir", required=True, help="where to write input log files")
    g.add_argument("--labels-dir", required=True, help="where to write expected_labels.json (kept separate)")
    return p


def split_sql(sql_text: str) -> list:
    """Split a SQL script into statements using SQLite's own tokenizer, so a ';' inside a string literal or a
    comment does not end a statement. Comment-only chunks are dropped."""
    import sqlite3
    statements, buf = [], ""
    for line in sql_text.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            statements.append(buf.strip())
            buf = ""
    if buf.strip():
        statements.append(buf.strip())
    return [st for st in statements if any(
        ln.strip() and not ln.strip().startswith("--") for ln in st.splitlines())]


def _query(args) -> int:
    import sqlite3
    if not Path(args.db).exists():
        print(f"error: database not found: {args.db}", file=sys.stderr)
        return 2
    sql_text = args.sql if args.sql else Path(args.file).read_text(encoding="utf-8")
    statements = split_sql(sql_text)
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    for stmt in statements:
        try:
            cur = conn.execute(stmt)
        except sqlite3.Error as exc:
            print(f"error: {exc}\n  in statement: {stmt[:200]}", file=sys.stderr)
            conn.close()
            return 2
        cols = [d[0] for d in cur.description or []]
        rows = cur.fetchall()
        table = [cols] + [["" if v is None else str(v) for v in r] for r in rows]
        widths = [max(len(r[i]) for r in table) for i in range(len(cols))]
        print()
        for n, row in enumerate(table):
            print("  ".join(v.ljust(w) for v, w in zip(row, widths)).rstrip())
            if n == 0:
                print("  ".join("-" * w for w in widths))
    conn.close()
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "generate":
        labels = generate(args.rows, args.seed, args.out_dir, args.labels_dir, args.files)
        print(json.dumps(labels, indent=2))
        return 0

    if args.command == "query":
        return _query(args)

    # Validate user-supplied paths first so a typo gives a clear message instead of a Python traceback.
    if not Path(args.services).is_file():
        print(f"error: services file not found: {args.services} "
              "(run from the project root or pass --services PATH)", file=sys.stderr)
        return 2
    labels = None
    if args.labels:
        try:
            labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"error: cannot read --labels file {args.labels!r}: {exc}", file=sys.stderr)
            return 2

    try:
        as_of = normalize_timestamp(args.as_of) if args.as_of else None
    except ValueError:
        print(f"error: --as-of is not a valid timestamp: {args.as_of!r}", file=sys.stderr)
        return 2

    out_dir = Path(args.out)
    _setup_logging(out_dir, args.verbose)
    try:
        result = run_pipeline(args.input, args.db, out_dir, args.services, run_id=args.run_id,
                              as_of=as_of, force=args.force, cfg=Config(clf_service=args.clf_service), labels=labels)
        m = result.metrics
        print(f"run_id={m['run_id']} status={m['status']} files={m['files_total']} read={m['rows_read']} "
              f"loaded={m['rows_loaded']} duplicate={m['rows_duplicate']} rejected={m['rows_rejected']} "
              f"reconciliation={'PASS' if result.checks_passed else 'FAIL'} duration={m['duration_seconds']}s")
        if m["failure_reason"]:
            print(f"failure_reason: {m['failure_reason']}", file=sys.stderr)
        return 0 if result.status == "success" and result.checks_passed else 1
    finally:
        _close_logging()
