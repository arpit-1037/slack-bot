"""Testable examples for safe repository modification workflows."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.modification.change_validator import ChangeValidator
from src.modification.code_modifier import CodeModifier
from src.modification.diff_generator import DiffGenerator
from src.modification.file_editor import BlindOverwriteError, SafeFileEditor
from src.modification.modification_models import CodePatch, PatchChange
from src.modification.patch_applier import ApprovalRequiredError, PatchApplier
from src.modification.patch_generator import PatchGenerator, PatchOperation
from src.modification.repository_modifier import RepositoryModifier
from src.modification.safety_guard import SafetyGuard
from src.router.intent_router import IntentRouter
from src.tools.git_tool import is_git_action_query


class FakeRouter:
    """ProviderRouter-compatible fake for deterministic patch generation tests."""

    name = "fake"

    def __init__(self, response: dict) -> None:
        self.response = response

    def complete(self, messages: list[dict], request_id: str | None = None) -> str:
        """Return the configured JSON response."""
        return json.dumps(self.response)


class SafeFileEditorTest(unittest.TestCase):
    """Examples for guarded writes, backups, and rollback."""

    def test_write_requires_checksum_and_can_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "example.py").write_text("value = 1\n", encoding="utf-8")
            editor = SafeFileEditor(repo_root=root, backup_root=root / "backups")
            snapshot = editor.read_file("example.py")

            with self.assertRaises(BlindOverwriteError):
                editor.write_file("example.py", "value = 2\n")

            change = editor.write_file(
                "example.py",
                "value = 2\n",
                expected_checksum=snapshot.checksum,
                encoding=snapshot.encoding,
            )
            self.assertEqual((root / "example.py").read_text(encoding="utf-8"), "value = 2\n")

            editor.rollback([change])
            self.assertEqual((root / "example.py").read_text(encoding="utf-8"), "value = 1\n")


class PatchGeneratorTest(unittest.TestCase):
    """Examples for minimal function-level patch operations."""

    def test_function_level_replacement_preserves_unrelated_code(self) -> None:
        original = (
            "def greet(name):\n"
            "    return 'hello'\n"
            "\n"
            "def untouched():\n"
            "    return 42\n"
        )
        operation = PatchOperation(
            op="replace",
            path="example.py",
            target_type="function",
            target="greet",
            content="def greet(name):\n    return f'hello {name}'\n",
        )

        proposed = PatchGenerator().apply_operations({"example.py": original}, [operation])
        self.assertIn("return f'hello {name}'", proposed["example.py"] or "")
        self.assertIn("def untouched():", proposed["example.py"] or "")


class DiffGeneratorTest(unittest.TestCase):
    """Examples for reviewable unified diffs and summaries."""

    def test_generates_unified_diff_and_summary(self) -> None:
        patch = CodePatch(
            summary="Update example value.",
            changes=[
                PatchChange(
                    file_path="example.py",
                    old_content="value = 1\n",
                    new_content="value = 2\n",
                    modification_reason="Use the corrected value.",
                )
            ],
        )

        diff_generator = DiffGenerator()
        diff = diff_generator.generate_diff(patch)
        summaries = diff_generator.summarize_changes(patch)

        self.assertIn("--- a/example.py", diff)
        self.assertIn("+value = 2", diff)
        self.assertEqual(summaries[0], "example.py: modified, +1/-1 - Use the corrected value.")


class SafetyGuardTest(unittest.TestCase):
    """Examples for protected paths and approval requirements."""

    def test_secret_file_change_is_not_safe(self) -> None:
        patch = CodePatch(
            summary="Update env.",
            changes=[
                PatchChange(
                    file_path=".env",
                    old_content="TOKEN=old\n",
                    new_content="TOKEN=new\n",
                    modification_reason="Rotate token.",
                )
            ],
        )

        result = SafetyGuard().validate_modification(patch)

        self.assertFalse(result.ok)
        self.assertIn("Protected secret", result.issues[0].reason)


class PatchApplierTest(unittest.TestCase):
    """Examples for explicit approval, backups, and rollback."""

    def test_apply_requires_approval_and_can_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "example.py").write_text("value = 1\n", encoding="utf-8")
            patch = CodePatch(
                summary="Update value.",
                changes=[
                    PatchChange(
                        file_path="example.py",
                        old_content="value = 1\n",
                        new_content="value = 2\n",
                        modification_reason="Use the corrected value.",
                    )
                ],
            )
            applier = PatchApplier(
                file_editor=SafeFileEditor(repo_root=root, backup_root=root / "backups")
            )

            with self.assertRaises(ApprovalRequiredError):
                applier.apply_patch(patch, str(root))

            applied = applier.apply_patch(patch, str(root), approved=True)
            self.assertEqual((root / "example.py").read_text(encoding="utf-8"), "value = 2\n")

            applier.rollback_patch(applied, str(root))
            self.assertEqual((root / "example.py").read_text(encoding="utf-8"), "value = 1\n")


class ChangeValidatorTest(unittest.TestCase):
    """Examples for syntax validation before apply."""

    def test_python_syntax_failure_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = ChangeValidator().validate(
                project_path=tmp,
                proposed_files={"broken.py": "def broken(:\n    pass\n"},
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.errors[0].check, "python-syntax")


class RepositoryModifierTest(unittest.TestCase):
    """End-to-end example with fake patch generation and safe apply."""

    def test_modifier_generates_validates_and_applies_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample.py").write_text(
                "def total(values):\n"
                "    return 0\n",
                encoding="utf-8",
            )
            router = FakeRouter(
                {
                    "summary": "Fix total calculation.",
                    "risk_notes": [],
                    "operations": [
                        {
                            "op": "replace",
                            "path": "sample.py",
                            "target_type": "function",
                            "target": "total",
                            "content": "def total(values):\n    return sum(values)\n",
                            "reason": "Return the sum instead of a constant.",
                        }
                    ],
                }
            )
            modifier = RepositoryModifier(
                patch_generator=PatchGenerator(provider_router=router),
                file_editor=SafeFileEditor(repo_root=root, backup_root=root / "backups"),
            )

            result = modifier.modify_repository(
                project_path=str(root),
                task="fix bug in sample.py total function",
                preview_only=False,
            )

            self.assertTrue(result.applied)
            self.assertIn("return sum(values)", (root / "sample.py").read_text(encoding="utf-8"))
            self.assertIn("--- a/sample.py", result.diff_preview)


class CodeModifierTest(unittest.TestCase):
    """End-to-end example for preview-only modification orchestration."""

    def test_code_modifier_generates_diff_without_applying(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample.py").write_text(
                "def total(values):\n"
                "    return 0\n",
                encoding="utf-8",
            )
            router = FakeRouter(
                {
                    "summary": "Fix total calculation.",
                    "risk_notes": [],
                    "operations": [
                        {
                            "op": "replace",
                            "path": "sample.py",
                            "target_type": "function",
                            "target": "total",
                            "content": "def total(values):\n    return sum(values)\n",
                            "reason": "Return the sum instead of a constant.",
                        }
                    ],
                }
            )
            modifier = CodeModifier(
                patch_generator=PatchGenerator(provider_router=router),
                file_editor=SafeFileEditor(repo_root=root, backup_root=root / "backups"),
            )

            result = modifier.preview_changes(
                project_path=str(root),
                user_request="update sample.py total function",
            )

            self.assertFalse(result.applied)
            self.assertTrue(result.approval_required)
            self.assertEqual((root / "sample.py").read_text(encoding="utf-8"), "def total(values):\n    return 0\n")
            self.assertIn("+    return sum(values)", result.unified_diff)
            self.assertIn("Generated a reviewable diff only", result.message)


class ModificationRoutingTest(unittest.TestCase):
    """Examples for routing modification tasks away from naive git actions."""

    def test_add_tests_routes_to_repository_modifier_not_git_add(self) -> None:
        task = "add tests for src/modification/file_editor.py"

        self.assertFalse(is_git_action_query(task))
        self.assertEqual(IntentRouter().classify(task), "project_modify")


if __name__ == "__main__":
    unittest.main()
