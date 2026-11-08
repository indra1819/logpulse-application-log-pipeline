import json
import tempfile
import unittest
from pathlib import Path

from logpipe.generator import generate
from logpipe.pipeline import run_pipeline
from tests.helpers import AS_OF, SERVICES_CSV


class GeneratorTests(unittest.TestCase):
    def test_same_seed_gives_byte_identical_output(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            generate(500, 11, f"{a}/in", f"{a}/lab", 3)
            generate(500, 11, f"{b}/in", f"{b}/lab", 3)
            for p in sorted(Path(f"{a}/in").iterdir()):
                self.assertEqual(p.read_bytes(), (Path(f"{b}/in") / p.name).read_bytes())

    def test_different_seed_changes_data(self):
        with tempfile.TemporaryDirectory() as a:
            generate(200, 1, f"{a}/in1", f"{a}/l1", 2)
            generate(200, 2, f"{a}/in2", f"{a}/l2", 2)
            self.assertNotEqual((Path(a) / "in1/app_logs_000.log").read_bytes(), (Path(a) / "in2/app_logs_000.log").read_bytes())

    def test_pipeline_output_matches_the_separate_labels_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            labels = generate(2000, 5, root / "in", root / "labels", 4)
            self.assertNotIn("expected", "".join(p.name for p in (root / "in").iterdir()))  # labels kept apart
            services = root / "services.csv"
            services.write_text(SERVICES_CSV + "orders,commerce\nnotifications,messaging\ninventory,supply\n")
            result = run_pipeline(root / "in", root / "db.sqlite", root / "out", services, as_of=AS_OF, labels=labels)
            self.assertEqual(result.metrics["rows_loaded"], labels["expected_loaded"])
            self.assertEqual(result.metrics["rows_duplicate"], labels["expected_duplicates"])
            self.assertEqual(result.metrics["rows_read"], labels["total_data_lines"])
            self.assertEqual(result.metrics["blank_lines"], labels["blank_lines"])
            failing = [c for c in result.checks if not c["passed"]]
            self.assertEqual(failing, [], failing)


if __name__ == "__main__":
    unittest.main()
