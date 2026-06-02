"""Approved patch application with backups and rollback support."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from src.modification.file_editor import AppliedFileChange, FileBackup, FileUpdate, SafeFileEditor
from src.modification.modification_models import CodePatch, PatchChange
from src.modification.safety_guard import SafetyGuard, SafetyResult
from src.utils.helpers import get_logger

log = get_logger(__name__)


class PatchApplyError(RuntimeError):
    """Raised when a patch cannot be applied safely."""


class ApprovalRequiredError(PatchApplyError):
    """Raised when a patch is applied without explicit approval."""


@dataclass(frozen=True)
class AppliedPatch:
    """Applied patch metadata used for rollback."""

    patch: CodePatch
    applied_changes: list[AppliedFileChange] = field(default_factory=list)
    safety: SafetyResult = field(default_factory=SafetyResult)

    @property
    def changed_paths(self) -> list[str]:
        """Return changed repository paths in stable order."""
        return sorted(change.path for change in self.applied_changes)


class PatchApplier:
    """Apply approved code patches using guarded file writes."""

    def __init__(
        self,
        file_editor: SafeFileEditor | None = None,
        safety_guard: SafetyGuard | None = None,
    ) -> None:
        self.file_editor = file_editor or SafeFileEditor()
        self.safety_guard = safety_guard or SafetyGuard()

    def apply_patch(
        self,
        patch: CodePatch,
        project_path: str,
        approved: bool = False,
    ) -> AppliedPatch:
        """Apply a patch only after safety validation and explicit approval."""
        safety = self.safety_guard.validate_modification(patch, project_path=project_path)
        if not safety.ok:
            raise PatchApplyError("Patch failed safety validation.")
        if safety.approval_required and not approved:
            raise ApprovalRequiredError("Patch requires explicit approval before application.")

        updates = [
            self._update_from_change(change, project_path)
            for change in patch.changes
            if change.old_content != change.new_content
        ]
        applied_changes = self.file_editor.apply_file_changes(updates, repo_root=project_path)
        log.info("Applied approved patch files=%d", len(applied_changes))
        return AppliedPatch(patch=patch, applied_changes=applied_changes, safety=safety)

    def rollback_patch(
        self,
        applied_patch: AppliedPatch | Iterable[AppliedFileChange],
        project_path: str,
    ) -> None:
        """Rollback a previously applied patch."""
        if isinstance(applied_patch, AppliedPatch):
            changes = applied_patch.applied_changes
        else:
            changes = list(applied_patch)
        self.file_editor.rollback(changes, repo_root=project_path)
        log.info("Rolled back patch files=%d", len(changes))

    def backup_file(self, file_path: str, project_path: str) -> FileBackup:
        """Create a rollback backup for a repository file."""
        return self.file_editor.create_backup(file_path, repo_root=project_path)

    def _update_from_change(self, change: PatchChange, project_path: str) -> FileUpdate:
        absolute = self.file_editor.resolve_path(change.file_path, repo_root=project_path)
        relative = self.file_editor.relative_path(absolute, repo_root=project_path)

        if absolute.exists():
            snapshot = self.file_editor.read_file(relative, repo_root=project_path)
            if change.old_content is not None and snapshot.content != change.old_content:
                raise PatchApplyError(
                    f"Refusing to apply {relative}: file content changed after patch generation."
                )
            return FileUpdate(
                path=relative,
                content=change.new_content,
                expected_checksum=snapshot.checksum,
                encoding=snapshot.encoding,
            )

        if change.old_content is not None:
            raise PatchApplyError(f"Refusing to apply {relative}: expected an existing file.")
        return FileUpdate(
            path=relative,
            content=change.new_content,
            expected_checksum=None,
            encoding="utf-8",
        )


def apply_patch(patch: CodePatch, project_path: str, approved: bool = False) -> AppliedPatch:
    """Convenience wrapper for approved patch application."""
    return PatchApplier().apply_patch(patch, project_path=project_path, approved=approved)


def rollback_patch(applied_patch: AppliedPatch | Iterable[AppliedFileChange], project_path: str) -> None:
    """Convenience wrapper for patch rollback."""
    PatchApplier().rollback_patch(applied_patch, project_path=project_path)


def backup_file(file_path: str, project_path: str) -> FileBackup:
    """Convenience wrapper for creating a file backup."""
    return PatchApplier().backup_file(file_path, project_path=project_path)
