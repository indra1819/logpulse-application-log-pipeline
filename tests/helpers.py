import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from logpipe.pipeline import run_pipeline

AS_OF = datetime(2026, 11, 7, tzinfo=timezone.utc)
SERVICES_CSV = "service,owner_team\nauth,identity\npayments,billing\nsearch,discovery\n"


def jl(ts="2026-11-01T10:00:00Z", level="INFO", service="auth", **extra) -> str:
    """Build one JSON log line. Pass key=None to produce an explicit null."""
    rec = {"timestamp": ts, "level": level, "service": service}
    rec.update(extra)
    return json.dumps(rec)


class PipelineTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.input = root / "input"
        self.input.mkdir()
        self.out = root / "out"
        self.db = root / "db.sqlite"
        self.services = root / "services.csv"
        self.services.write_text(SERVICES_CSV)

    def write_log(self, name, lines, newline="\n"):
        path = self.input / name
        path.write_bytes((newline.join(lines) + newline).encode("utf-8") if lines else b"")
        return path

    def run_pipeline(self, input_path=None, **kw):
        kw.setdefault("as_of", AS_OF)
        return run_pipeline(input_path or self.input, self.db, self.out, self.services, **kw)

    def query(self, sql, params=()):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def scalar(self, sql, params=()):
        return self.query(sql, params)[0][0]
