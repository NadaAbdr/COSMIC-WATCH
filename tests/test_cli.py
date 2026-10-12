"""Tests for pipeline.cli: help text, unknown commands and the silver-table checks."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from pipeline import cli


def run_quietly(func, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = func(*args)
    return code, out.getvalue()


def write_silver(folder, dim_ids, fact_ids):
    folder = Path(folder)
    pd.DataFrame({"asteroid_id": dim_ids}).to_csv(folder / "dim_asteroids.csv", index=False)
    pd.DataFrame(
        {
            "asteroid_id": fact_ids,
            "close_approach_date": [f"2026-01-{i + 1:02d}" for i in range(len(fact_ids))],
            "velocity_kmh": [1000.0 + i for i in range(len(fact_ids))],
        }
    ).to_csv(folder / "fact_close_approaches.csv", index=False)


class MainTests(unittest.TestCase):
    def test_help_returns_zero_and_lists_commands(self):
        code, output = run_quietly(cli.main, ["--help"])
        self.assertEqual(code, 0)
        for command in ("extract", "details", "transform", "validate", "load"):
            self.assertIn(command, output)

    def test_no_arguments_shows_help(self):
        code, _ = run_quietly(cli.main, [])
        self.assertEqual(code, 0)

    def test_unknown_command_returns_two(self):
        code, output = run_quietly(cli.main, ["bogus"])
        self.assertEqual(code, 2)
        self.assertIn("Unknown command", output)


class ValidateTests(unittest.TestCase):
    def test_clean_tables_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_silver(tmp, dim_ids=[1, 2], fact_ids=[1, 1, 2])
            code, output = run_quietly(cli.validate, tmp)
        self.assertEqual(code, 0)
        self.assertNotIn("FAIL", output)

    def test_fact_row_without_a_dim_asteroid_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_silver(tmp, dim_ids=[1, 2], fact_ids=[1, 3])
            code, output = run_quietly(cli.validate, tmp)
        self.assertEqual(code, 1)
        self.assertIn("FAIL  no fact rows without a dim asteroid", output)

    def test_duplicate_dim_id_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_silver(tmp, dim_ids=[1, 1], fact_ids=[1])
            code, output = run_quietly(cli.validate, tmp)
        self.assertEqual(code, 1)
        self.assertIn("FAIL  dim asteroid_id is unique", output)

    def test_missing_files_return_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, output = run_quietly(cli.validate, tmp)
        self.assertEqual(code, 1)
        self.assertIn("Run 'transform' first", output)


if __name__ == "__main__":
    unittest.main()
