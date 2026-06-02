"""Testable examples for validation and verification workflows."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from src.modification.modification_models import CodePatch, PatchChange
from src.validation.import_checker import ImportChecker
from src.validation.lint_runner import LintRunner
from src.validation.syntax_validator import SyntaxValidator
from src.validation.test_runner import TestRunner
from src.validation.validation_engine import ValidationEngine
from src.validation.validation_models import STATUS_FAIL, STATUS_PASS, STATUS_SKIPPED


class SyntaxValidatorTest(unittest.TestCase):
    """Examples for source and configuration syntax validation."""

    def test_python_syntax_error_reports_location(self) -> None:
        result = SyntaxValidator().validate_python("def broken(:\n    pass\n", "broken.py")

        self.assertEqual(result.status, STATUS_FAIL)
        self.assertEqual(result.errors[0].line, 1)
        self.assertEqual(result.errors[0].check, "python-syntax")

    def test_json_syntax_passes(self) -> None:
        result = SyntaxValidator().validate_json('{"ok": true}', "config.json")

        self.assertEqual(result.status, STATUS_PASS)
        self.assertFalse(result.errors)


class ImportCheckerTest(unittest.TestCase):
    """Examples for missing and circular import validation."""

    def test_missing_local_import_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "__init__.py").write_text("", encoding="utf-8")
            (root / "src" / "app.py").write_text(
                "from src.missing import value\n",
                encoding="utf-8",
            )

            result = ImportChecker().validate_imports(str(root))

            self.assertEqual(result.status, STATUS_FAIL)
            self.assertEqual(result.errors[0].check, "local-import")

    def test_circular_import_is_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("import b\n", encoding="utf-8")
            (root / "b.py").write_text("import a\n", encoding="utf-8")

            result = ImportChecker().validate_imports(str(root))

            self.assertTrue(result.circular_imports)
            self.assertTrue(result.warnings)


class TestRunnerTest(unittest.TestCase):
    """Examples for safe test command execution."""

    def test_runs_unittest_with_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "tests" / "test_sample.py").write_text(
                "import unittest\n"
                "\n"
                "class SampleTest(unittest.TestCase):\n"
                "    def test_ok(self):\n"
                "        self.assertTrue(True)\n",
                encoding="utf-8",
            )

            result = TestRunner(timeout_seconds=5).run_tests(
                str(root),
                command=[sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            )

            self.assertEqual(result.status, STATUS_PASS)
            self.assertEqual(result.passed_tests, 1)


class LintRunnerTest(unittest.TestCase):
    """Examples for lint detection and safe skipped results."""

    def test_lint_runner_skips_unknown_linter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = LintRunner().run_linting(str(Path(tmp)), linter="unknown")

            self.assertEqual(result.status, STATUS_SKIPPED)
            self.assertIn("Unsupported linter", result.summary)


class ValidationEngineTest(unittest.TestCase):
    """Examples for patch validation and reporting."""

    def test_validate_patch_reports_syntax_failure_without_applying(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample.py").write_text("value = 1\n", encoding="utf-8")
            patch = CodePatch(
                summary="Break syntax.",
                changes=[
                    PatchChange(
                        file_path="sample.py",
                        old_content="value = 1\n",
                        new_content="def broken(:\n    pass\n",
                    )
                ],
            )
            engine = ValidationEngine(run_tests_by_default=False, run_lint_by_default=False)

            report = engine.validate_patch(patch, str(root))

            self.assertEqual(report.status, STATUS_FAIL)
            self.assertIn("Validation Summary", report.report_text)
            self.assertEqual((root / "sample.py").read_text(encoding="utf-8"), "value = 1\n")

    def test_validate_repository_can_skip_tests_and_lint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample.py").write_text("value = 1\n", encoding="utf-8")
            engine = ValidationEngine(run_tests_by_default=False, run_lint_by_default=False)

            report = engine.validate_repository(str(root))

            self.assertEqual(report.status, STATUS_PASS)
            self.assertIn("Syntax", report.report_text)


if __name__ == "__main__":
    unittest.main()
