"""Main orchestrator for safe repository modifications."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

from src.debugging.bug_context_builder import BugContextBuilder
from src.debugging.stacktrace_parser import StacktraceParser
from src.modification.change_validator import ChangeValidator, ValidationIssue, ValidationResult
from src.modification.diff_manager import DiffManager
from src.modification.file_editor import AppliedFileChange, FileSnapshot, FileUpdate, SafeFileEditor
from src.modification.patch_generator import PatchApplicationError, PatchGenerationError, PatchGenerator, PatchSet
from src.repository.context_selector import ContextSelector
from src.repository.repository_indexer import RepositoryIndexer
from src.utils.helpers import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class ModificationResult:
    """Outcome of a safe repository modification workflow."""

    summary: str
    patch_set: PatchSet
    diff_preview: str
    validation: ValidationResult
    applied: bool
    preview_only: bool = False
    rolled_back: bool = False
    context_files: list[str] = field(default_factory=list)
    applied_changes: list[AppliedFileChange] = field(default_factory=list)

    def format_response(self) -> str:
        """Format the result for Slack or CLI display."""
        status = "Applied" if self.applied else "Not applied"
        if self.preview_only:
            status = "Preview only"
        if self.rolled_back:
            status = "Rolled back"

        lines = [
            f"*Repository modification: {status}*",
            f"*Summary:* {self.summary}",
        ]
        if self.context_files:
            lines.append("*Context files:* " + ", ".join(self.context_files))
        if self.patch_set.risk_notes:
            lines.append("*Risk notes:*")
            lines.extend(f"- {note}" for note in self.patch_set.risk_notes)

        if self.validation.issues:
            lines.append("*Validation:*")
            lines.append(self.validation.format_report())
        else:
            lines.append("*Validation:* " + self.validation.format_report())

        if self.applied_changes:
            changed = ", ".join(change.path for change in self.applied_changes)
            lines.append("*Changed files:* " + changed)

        lines.append(self.diff_preview)
        return "\n\n".join(lines)


class RepositoryModifier:
    """Coordinate context selection, patch generation, validation, and safe apply."""

    def __init__(
        self,
        indexer: RepositoryIndexer | None = None,
        context_selector: ContextSelector | None = None,
        patch_generator: PatchGenerator | None = None,
        diff_manager: DiffManager | None = None,
        validator: ChangeValidator | None = None,
        file_editor: SafeFileEditor | None = None,
        stacktrace_parser: StacktraceParser | None = None,
        bug_context_builder: BugContextBuilder | None = None,
    ) -> None:
        self.indexer = indexer or RepositoryIndexer()
        self.context_selector = context_selector or ContextSelector(indexer=self.indexer)
        self.patch_generator = patch_generator or PatchGenerator()
        self.diff_manager = diff_manager or DiffManager()
        self.validator = validator or ChangeValidator(indexer=self.indexer)
        self.file_editor = file_editor or SafeFileEditor()
        self.stacktrace_parser = stacktrace_parser or StacktraceParser()
        self.bug_context_builder = bug_context_builder or BugContextBuilder(indexer=self.indexer)

    def modify(
        self,
        project_path: str,
        task: str,
        thread_ts: str | None = None,
        channel: str | None = None,
        slack_user: str | None = None,
        request_id: str | None = None,
        preview_only: bool = False,
        run_pytest: bool | None = None,
    ) -> str:
        """Run the modification workflow and return a human-readable response."""
        preview_only = preview_only or self._is_preview_request(task)
        result = self.modify_repository(
            project_path=project_path,
            task=task,
            request_id=request_id,
            preview_only=preview_only,
            run_pytest=run_pytest,
        )
        return result.format_response()

    def modify_repository(
        self,
        project_path: str,
        task: str,
        request_id: str | None = None,
        preview_only: bool = False,
        run_pytest: bool | None = None,
    ) -> ModificationResult:
        """Run analysis, patch generation, diff preview, validation, and safe apply."""
        project_path = os.path.abspath(os.path.expanduser(project_path))
        log.info("request_id=%s repository modifier started path=%s", request_id, project_path)

        self.indexer.ensure_index(project_path)
        selection = self.context_selector.select_context(project_path, task, request_id=request_id)
        debug_context = self._debug_context(project_path, task, request_id=request_id)
        try:
            patch_set = self.patch_generator.generate(
                task=task,
                project_path=project_path,
                context_selection=selection,
                debug_context=debug_context,
                request_id=request_id,
            )
        except PatchGenerationError as error:
            validation = ValidationResult(
                issues=[
                    ValidationIssue(
                        path="patch",
                        check="patch-generation",
                        message=str(error),
                    )
                ],
                checks_run=["patch-generation"],
            )
            return ModificationResult(
                summary="Patch generation failed.",
                patch_set=PatchSet(summary="Patch generation failed."),
                diff_preview="No files were changed.",
                validation=validation,
                applied=False,
                preview_only=True,
                context_files=[item.path for item in selection.selected_files],
            )
        patch_set = self._normalize_patch_set_paths(project_path, patch_set)
        patch_set = PatchSet(
            summary=patch_set.summary,
            operations=patch_set.operations,
            risk_notes=patch_set.risk_notes + self._operation_risk_notes(patch_set),
        )

        if not patch_set.operations:
            validation = ValidationResult(checks_run=["patch-generation"])
            return ModificationResult(
                summary=patch_set.summary,
                patch_set=patch_set,
                diff_preview="No patch operations were generated.",
                validation=validation,
                applied=False,
                preview_only=True,
                context_files=[item.path for item in selection.selected_files],
            )

        originals, snapshots = self._load_originals(project_path, patch_set)
        try:
            proposed = self.patch_generator.apply_operations(originals, patch_set.operations)
        except PatchApplicationError as error:
            validation = ValidationResult(
                issues=[
                    ValidationIssue(
                        path="patch",
                        check="patch-application",
                        message=str(error),
                    )
                ],
                checks_run=["patch-application"],
            )
            return ModificationResult(
                summary="Patch operations could not be applied safely.",
                patch_set=patch_set,
                diff_preview="No files were changed.",
                validation=validation,
                applied=False,
                preview_only=True,
                context_files=[item.path for item in selection.selected_files],
            )
        diff_preview = self.diff_manager.format_preview(originals, proposed, patch_set.operations)
        deleted_paths = {path for path, content in proposed.items() if content is None}
        should_run_pytest = run_pytest if run_pytest is not None else self._env_run_pytest()
        validation = self.validator.validate(
            project_path=project_path,
            proposed_files=proposed,
            deleted_paths=deleted_paths,
            run_pytest=False,
            request_id=request_id,
        )

        if not self.diff_manager.generate_diffs(originals, proposed):
            return ModificationResult(
                summary="No effective file changes were produced.",
                patch_set=patch_set,
                diff_preview=diff_preview,
                validation=validation,
                applied=False,
                preview_only=True,
                context_files=[item.path for item in selection.selected_files],
            )

        if not validation.ok or preview_only:
            return ModificationResult(
                summary=patch_set.summary,
                patch_set=patch_set,
                diff_preview=diff_preview,
                validation=validation,
                applied=False,
                preview_only=preview_only,
                context_files=[item.path for item in selection.selected_files],
            )

        updates = self._updates_from_proposed(proposed, originals, snapshots)
        applied_changes: list[AppliedFileChange] = []
        try:
            applied_changes = self.file_editor.apply_file_changes(updates, repo_root=project_path)
            post_validation = self.validator.validate(
                project_path=project_path,
                proposed_files=self._read_applied_files(project_path, proposed),
                deleted_paths=deleted_paths,
                run_pytest=should_run_pytest,
                request_id=request_id,
            )
            if not post_validation.ok:
                self.file_editor.rollback(applied_changes, repo_root=project_path)
                self.indexer.reindex(project_path)
                return ModificationResult(
                    summary=patch_set.summary,
                    patch_set=patch_set,
                    diff_preview=diff_preview,
                    validation=post_validation,
                    applied=False,
                    rolled_back=True,
                    context_files=[item.path for item in selection.selected_files],
                    applied_changes=applied_changes,
                )

            self.indexer.reindex(project_path)
            return ModificationResult(
                summary=patch_set.summary,
                patch_set=patch_set,
                diff_preview=diff_preview,
                validation=post_validation,
                applied=True,
                context_files=[item.path for item in selection.selected_files],
                applied_changes=applied_changes,
            )
        except Exception as error:
            log.exception("request_id=%s repository modification failed", request_id)
            if applied_changes:
                self.file_editor.rollback(applied_changes, repo_root=project_path)
            validation = ValidationResult(
                issues=[
                    *validation.issues,
                    ValidationIssue(
                        path="repository",
                        check="safe-apply",
                        message=str(error),
                    ),
                ],
                checks_run=[*validation.checks_run, "safe-apply"],
            )
            failed_patch_set = PatchSet(
                summary=f"{patch_set.summary} (apply failed: {error})",
                operations=patch_set.operations,
                risk_notes=patch_set.risk_notes,
            )
            return ModificationResult(
                summary=failed_patch_set.summary,
                patch_set=failed_patch_set,
                diff_preview=diff_preview,
                validation=validation,
                applied=False,
                rolled_back=bool(applied_changes),
                context_files=[item.path for item in selection.selected_files],
                applied_changes=applied_changes,
            )

    def _load_originals(
        self,
        project_path: str,
        patch_set: PatchSet,
    ) -> tuple[dict[str, str | None], dict[str, FileSnapshot]]:
        originals: dict[str, str | None] = {}
        snapshots: dict[str, FileSnapshot] = {}
        for path in patch_set.paths:
            absolute_path = self.file_editor.resolve_path(path, repo_root=project_path)
            relative_path = self.file_editor.relative_path(absolute_path, repo_root=project_path)
            if absolute_path.exists():
                snapshot = self.file_editor.read_file(relative_path, repo_root=project_path)
                originals[relative_path] = snapshot.content
                snapshots[relative_path] = snapshot
            else:
                originals[relative_path] = None
        return originals, snapshots

    def _normalize_patch_set_paths(self, project_path: str, patch_set: PatchSet) -> PatchSet:
        """Normalize provider paths to repository-relative paths before application."""
        operations = []
        for operation in patch_set.operations:
            absolute_path = self.file_editor.resolve_path(operation.path, repo_root=project_path)
            relative_path = self.file_editor.relative_path(absolute_path, repo_root=project_path)
            operations.append(replace(operation, path=relative_path))
        return PatchSet(
            summary=patch_set.summary,
            operations=operations,
            risk_notes=patch_set.risk_notes,
        )

    def _updates_from_proposed(
        self,
        proposed: dict[str, str | None],
        originals: dict[str, str | None],
        snapshots: dict[str, FileSnapshot],
    ) -> list[FileUpdate]:
        updates = []
        for path in sorted(proposed):
            if proposed[path] == originals.get(path):
                continue
            snapshot = snapshots.get(path)
            updates.append(
                FileUpdate(
                    path=path,
                    content=proposed[path],
                    expected_checksum=snapshot.checksum if snapshot else None,
                    encoding=snapshot.encoding if snapshot else "utf-8",
                )
            )
        return updates

    def _read_applied_files(
        self,
        project_path: str,
        proposed: dict[str, str | None],
    ) -> dict[str, str | None]:
        files: dict[str, str | None] = {}
        for path, content in proposed.items():
            if content is None:
                files[path] = None
            else:
                files[path] = self.file_editor.read_file(path, repo_root=project_path).content
        return files

    def _debug_context(
        self,
        project_path: str,
        task: str,
        request_id: str | None = None,
    ) -> str | None:
        task_lower = task.lower()
        if not any(word in task_lower for word in ("bug", "error", "failing", "failed", "fix", "traceback")):
            return None
        stacktrace = self.stacktrace_parser.parse(task)
        bug_context = self.bug_context_builder.build(
            project_path=project_path,
            bug_description=task,
            stacktrace=stacktrace,
            request_id=request_id,
        )
        return bug_context.format_context()

    def _operation_risk_notes(self, patch_set: PatchSet) -> list[str]:
        notes = []
        for operation in patch_set.operations:
            if operation.target_type == "file" and operation.op == "replace":
                notes.append(f"{operation.path}: full-file replacement requested; review diff carefully.")
            if operation.op == "delete" and operation.target_type == "file":
                notes.append(f"{operation.path}: file deletion requested; dependents will be validated.")
        return notes

    def _env_run_pytest(self) -> bool:
        return os.getenv("MODIFICATION_RUN_PYTEST", "").strip().lower() in {"1", "true", "yes", "on"}

    def _is_preview_request(self, task: str) -> bool:
        """Return True when the user asks to see changes without applying them."""
        task_lower = task.lower()
        preview_signals = [
            "preview only",
            "dry run",
            "do not apply",
            "don't apply",
            "show me the diff",
            "show diff",
            "what would you change",
        ]
        return any(signal in task_lower for signal in preview_signals)
