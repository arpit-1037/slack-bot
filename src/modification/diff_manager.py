"""Readable diff generation and modification summaries."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Mapping

from src.modification.patch_generator import PatchOperation
from src.utils.helpers import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class FileDiffSummary:
    """Line-level summary for one changed file."""

    path: str
    additions: int
    deletions: int
    operation_count: int
    status: str


class DiffManager:
    """Create git-style diffs, previews, and summaries for proposed changes."""

    def generate_file_diff(
        self,
        path: str,
        original: str | None,
        proposed: str | None,
    ) -> str:
        """Return a unified diff for one file."""
        if original == proposed:
            return ""

        original_lines = [] if original is None else original.splitlines(keepends=True)
        proposed_lines = [] if proposed is None else proposed.splitlines(keepends=True)
        from_file = "/dev/null" if original is None else f"a/{path}"
        to_file = "/dev/null" if proposed is None else f"b/{path}"
        return "".join(
            difflib.unified_diff(
                original_lines,
                proposed_lines,
                fromfile=from_file,
                tofile=to_file,
            )
        )

    def generate_diffs(
        self,
        originals: Mapping[str, str | None],
        proposed: Mapping[str, str | None],
    ) -> dict[str, str]:
        """Return unified diffs keyed by changed path."""
        paths = sorted(set(originals) | set(proposed))
        diffs = {
            path: self.generate_file_diff(path, originals.get(path), proposed.get(path))
            for path in paths
        }
        return {path: diff for path, diff in diffs.items() if diff}

    def summarize(
        self,
        diffs: Mapping[str, str],
        operations: list[PatchOperation],
    ) -> list[FileDiffSummary]:
        """Summarize additions, deletions, operation counts, and file status."""
        operation_counts: dict[str, int] = {}
        for operation in operations:
            operation_counts[operation.path] = operation_counts.get(operation.path, 0) + 1

        summaries = []
        for path, diff in sorted(diffs.items()):
            additions = 0
            deletions = 0
            status = "modified"
            for line in diff.splitlines():
                if line.startswith("--- /dev/null"):
                    status = "created"
                elif line.startswith("+++ /dev/null"):
                    status = "deleted"
                elif line.startswith("+") and not line.startswith("+++"):
                    additions += 1
                elif line.startswith("-") and not line.startswith("---"):
                    deletions += 1
            summaries.append(
                FileDiffSummary(
                    path=path,
                    additions=additions,
                    deletions=deletions,
                    operation_count=operation_counts.get(path, 0),
                    status=status,
                )
            )
        return summaries

    def format_preview(
        self,
        originals: Mapping[str, str | None],
        proposed: Mapping[str, str | None],
        operations: list[PatchOperation],
    ) -> str:
        """Return a human-readable preview with summary and git-style diff."""
        diffs = self.generate_diffs(originals, proposed)
        summaries = self.summarize(diffs, operations)
        if not diffs:
            return "No file changes proposed."

        summary_lines = [
            f"- {item.path}: {item.status}, +{item.additions}/-{item.deletions}, ops={item.operation_count}"
            for item in summaries
        ]
        diff_text = "\n".join(diffs[path].rstrip() for path in sorted(diffs)).strip()
        log.info("Built modification diff preview files=%d", len(diffs))
        return "Modification preview:\n" + "\n".join(summary_lines) + f"\n\n```diff\n{diff_text}\n```"
