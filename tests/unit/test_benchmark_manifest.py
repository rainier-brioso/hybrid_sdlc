"""Baseline template check; validator coverage pending BENCH-VALIDATOR-001."""

import json
import unittest
from pathlib import Path


class BenchmarkTemplateTests(unittest.TestCase):
    def test_template_is_explicitly_unready(self) -> None:
        root = Path(__file__).resolve().parents[2]
        manifest = json.loads((root / "benchmarks/benchmark_task.json").read_text())
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["status"], "template_unready")
        self.assertFalse(manifest["readiness"]["comparative_claim_allowed"])
