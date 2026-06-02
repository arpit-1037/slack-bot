"""Reviewable diff generation for proposed code patches."""

from __future__ import annotations

import difflib

from src.modification.modification_models import CodePatch, PatchChange
from src.utils.helpers import get_logger

log = get_logger(__name__)


class DiffGenerator:
    """Generate human-readable unified diffs and summaries."""

    def generate_diff(self, patch: CodePatch) -> str:
        """Return a unified diff containing every changed file."""
        file_diffs = self.generate_file_diffs(patch)
        return "\n".join(file_diffs[path].rstrip() for path in sorted(file_diffs) if file_diffs[path]).strip()

    def generate_file_diffs(self, patch: CodePatch) -> dict[str, str]:
        """Return unified diffs keyed by repository-relative file path."""
        diffs = {}
        for change in patch.changes:
            diff = self._change_diff(change)
            if diff:
                diffs[change.file_path] = diff
        log.info("Generated review diffs files=%d", len(diffs))
        return diffs

    def summarize_changes(self, patch: CodePatch) -> list[str]:
        """Return concise file-by-file change summaries."""
        summaries = []
        for change in sorted(patch.changes, key=lambda item: item.file_path):
            if change.old_content == change.new_content:
                continue
            additions, deletions = self._line_counts(change)
            status = self._status(change)
            reason = f" - {change.modification_reason}" if change.modification_reason else ""
            summaries.append(
                f"{change.file_path}: {status}, +{additions}/-{deletions}{reason}"
            )
        return summaries

    def _change_diff(self, change: PatchChange) -> str:
        if change.old_content == change.new_content:
            return ""

        original_lines = [] if change.old_content is None else change.old_content.splitlines(keepends=True)
        proposed_lines = [] if change.new_content is None else change.new_content.splitlines(keepends=True)
        from_file = "/dev/null" if change.old_content is None else f"a/{change.file_path}"
        to_file = "/dev/null" if change.new_content is None else f"b/{change.file_path}"
        return "".join(
            difflib.unified_diff(
                original_lines,
                proposed_lines,
                fromfile=from_file,
                tofile=to_file,
            )
        )

    def _line_counts(self, change: PatchChange) -> tuple[int, int]:
        diff = self._change_diff(change)
        additions = 0
        deletions = 0
        for line in diff.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                additions += 1
            elif line.startswith("-") and not line.startswith("---"):
                deletions += 1
        return additions, deletions

    def _status(self, change: PatchChange) -> str:
        if change.is_creation:
            return "created"
        if change.is_deletion:
            return "deleted"
        return "modified"


def generate_diff(patch: CodePatch) -> str:
    """Convenience wrapper for unified diff generation."""
    return DiffGenerator().generate_diff(patch)


def summarize_changes(patch: CodePatch) -> list[str]:
    """Convenience wrapper for file-by-file change summaries."""
    return DiffGenerator().summarize_changes(patch)
