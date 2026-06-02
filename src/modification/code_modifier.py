"""Preview-first orchestrator for safe code modification."""

from __future__ import annotations

import os
from dataclasses import asdict, replace

from src.debugging.bug_context_builder import BugContextBuilder
from src.debugging.stacktrace_parser import StacktraceParser
from src.modification.change_validator import ChangeValidator, ValidationIssue, ValidationResult
from src.modification.diff_generator import DiffGenerator
from src.modification.file_editor import FileSnapshot, SafeFileEditor
from src.modification.modification_models import CodePatch, ModificationRequest, ModificationResult, PatchChange
from src.modification.patch_applier import AppliedPatch, PatchApplier
from src.modification.patch_generator import PatchApplicationError, PatchGenerationError, PatchGenerator, PatchSet
from src.modification.safety_guard import SafetyGuard
from src.repository.context_selector import ContextSelection, ContextSelector, SelectedFile
from src.repository.repository_indexer import RepositoryIndexer
from src.utils.helpers import get_logger

log = get_logger(__name__)


class CodeModifier:
    """Generate safe patch previews and defer application until approval."""

    def __init__(
        self,
        indexer: RepositoryIndexer | None = None,
        context_selector: ContextSelector | None = None,
        patch_generator: PatchGenerator | None = None,
        diff_generator: DiffGenerator | None = None,
        safety_guard: SafetyGuard | None = None,
        patch_applier: PatchApplier | None = None,
        file_editor: SafeFileEditor | None = None,
        validator: ChangeValidator | None = None,
        stacktrace_parser: StacktraceParser | None = None,
        bug_context_builder: BugContextBuilder | None = None,
    ) -> None:
        self.indexer = indexer or RepositoryIndexer()
        self.context_selector = context_selector or ContextSelector(indexer=self.indexer)
        self.patch_generator = patch_generator or PatchGenerator()
        self.diff_generator = diff_generator or DiffGenerator()
        self.safety_guard = safety_guard or SafetyGuard()
        self.file_editor = file_editor or SafeFileEditor()
        self.patch_applier = patch_applier or PatchApplier(
            file_editor=self.file_editor,
            safety_guard=self.safety_guard,
        )
        self.validator = validator or ChangeValidator(indexer=self.indexer)
        self.stacktrace_parser = stacktrace_parser or StacktraceParser()
        self.bug_context_builder = bug_context_builder or BugContextBuilder(indexer=self.indexer)

    def modify_code(
        self,
        project_path: str,
        user_request: str,
        repository_context: str | None = None,
        selected_files: list[str] | None = None,
        thread_ts: str | None = None,
        channel: str | None = None,
        slack_user: str | None = None,
        request_id: str | None = None,
    ) -> ModificationResult:
        """Generate a code modification preview and wait for approval."""
        return self.preview_changes(
            project_path=project_path,
            user_request=user_request,
            repository_context=repository_context,
            selected_files=selected_files,
            request_id=request_id,
        )

    def preview_changes(
        self,
        project_path: str,
        user_request: str | ModificationRequest,
        repository_context: str | None = None,
        selected_files: list[str] | None = None,
        request_id: str | None = None,
    ) -> ModificationResult:
        """Generate patch, diff, safety, and validation output without applying files."""
        project_path = os.path.abspath(os.path.expanduser(project_path))
        request = self._modification_request(
            project_path=project_path,
            user_request=user_request,
            repository_context=repository_context,
            selected_files=selected_files,
            request_id=request_id,
        )
        log.info("request_id=%s code modification preview started path=%s", request.request_id, project_path)

        try:
            selection = self._context_selection(project_path, request)
            request = replace(
                request,
                repository_context=selection.context,
                selected_files=[item.path for item in selection.selected_files],
            )
            patch_set = self.patch_generator.generate(
                task=request.user_request,
                project_path=project_path,
                context_selection=selection,
                debug_context=self._debug_context(project_path, request.user_request, request.request_id),
                request_id=request.request_id,
            )
            patch_set = self._normalize_patch_set_paths(project_path, patch_set)
            return self._preview_from_patch_set(project_path, request, patch_set)
        except PatchGenerationError as error:
            return self._failure_result(request, "Patch generation failed.", "patch-generation", str(error))
        except PatchApplicationError as error:
            return self._failure_result(request, "Patch operations could not be previewed.", "patch-preview", str(error))
        except Exception as error:
            log.exception("request_id=%s code modification preview failed", request.request_id)
            return self._failure_result(request, "Modification preview failed.", "preview", str(error))

    def apply_changes(
        self,
        patch: CodePatch,
        project_path: str,
        approved: bool = False,
    ) -> AppliedPatch:
        """Apply a previously previewed patch only when approved."""
        return self.patch_applier.apply_patch(patch, project_path=project_path, approved=approved)

    def _preview_from_patch_set(
        self,
        project_path: str,
        request: ModificationRequest,
        patch_set: PatchSet,
    ) -> ModificationResult:
        if not patch_set.operations:
            patch = CodePatch(
                summary=patch_set.summary,
                changes=[],
                modification_reason=request.user_request,
                request_id=request.request_id,
                approval_required=False,
                metadata={"risk_notes": patch_set.risk_notes},
            )
            return ModificationResult(
                request=request,
                patch=patch,
                validation_report="No patch operations were generated.",
                validation_ok=True,
                applied=False,
                approval_required=False,
                message="No files would be changed.",
            )

        originals, snapshots = self._load_originals(project_path, patch_set)
        proposed = self.patch_generator.apply_operations(originals, patch_set.operations)
        patch = self._code_patch_from_proposed(
            request=request,
            patch_set=patch_set,
            originals=originals,
            proposed=proposed,
        )
        file_diffs = self.diff_generator.generate_file_diffs(patch)
        unified_diff = self.diff_generator.generate_diff(patch)
        safety = self.safety_guard.validate_modification(patch, project_path=project_path)
        deleted_paths = {path for path, content in proposed.items() if content is None}
        validation = self.validator.validate(
            project_path=project_path,
            proposed_files=proposed,
            deleted_paths=deleted_paths,
            run_pytest=False,
            request_id=request.request_id,
        )

        return ModificationResult(
            request=request,
            patch=patch,
            unified_diff=unified_diff,
            file_diffs=file_diffs,
            change_summaries=self.diff_generator.summarize_changes(patch),
            safety_issues=safety.issues,
            validation_report=validation.format_report(),
            validation_ok=validation.ok,
            applied=False,
            approval_required=safety.approval_required,
            message="Generated a reviewable diff only; no files were modified.",
            metadata={
                "context_files": request.selected_files,
                "checks_run": validation.checks_run,
                "snapshots": sorted(snapshots),
                "risk_notes": patch_set.risk_notes,
            },
        )

    def _code_patch_from_proposed(
        self,
        request: ModificationRequest,
        patch_set: PatchSet,
        originals: dict[str, str | None],
        proposed: dict[str, str | None],
    ) -> CodePatch:
        changes = []
        operations_by_path = self._operations_by_path(patch_set)
        for path in sorted(set(originals) | set(proposed)):
            old_content = originals.get(path)
            new_content = proposed.get(path)
            if old_content == new_content:
                continue
            operations = operations_by_path.get(path, [])
            reason = "; ".join(operation.reason for operation in operations if operation.reason)
            changes.append(
                PatchChange(
                    file_path=path,
                    old_content=old_content,
                    new_content=new_content,
                    diff_summary=reason or self._change_type(old_content, new_content),
                    modification_reason=reason or request.user_request,
                    change_type=self._change_type(old_content, new_content),
                    metadata={"operations": [asdict(operation) for operation in operations]},
                )
            )

        return CodePatch(
            summary=patch_set.summary,
            changes=changes,
            diff_summary=patch_set.summary,
            modification_reason=request.user_request,
            request_id=request.request_id,
            approval_required=True,
            metadata={"risk_notes": patch_set.risk_notes},
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

    def _context_selection(
        self,
        project_path: str,
        request: ModificationRequest,
    ) -> ContextSelection:
        if request.repository_context:
            return ContextSelection(
                task=request.user_request,
                selected_files=[
                    SelectedFile(path=path, score=0, reasons=["provided"])
                    for path in request.selected_files
                ],
                context=request.repository_context,
            )
        return self.context_selector.select_context(
            project_path=project_path,
            task=request.user_request,
            request_id=request.request_id,
        )

    def _modification_request(
        self,
        project_path: str,
        user_request: str | ModificationRequest,
        repository_context: str | None,
        selected_files: list[str] | None,
        request_id: str | None,
    ) -> ModificationRequest:
        if isinstance(user_request, ModificationRequest):
            return replace(
                user_request,
                project_path=user_request.project_path or project_path,
                repository_context=repository_context if repository_context is not None else user_request.repository_context,
                selected_files=selected_files if selected_files is not None else user_request.selected_files,
                request_id=request_id or user_request.request_id,
            )
        return ModificationRequest(
            user_request=user_request,
            repository_context=repository_context or "",
            selected_files=selected_files or [],
            project_path=project_path,
            request_id=request_id,
        )

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

    def _failure_result(
        self,
        request: ModificationRequest,
        summary: str,
        check: str,
        message: str,
    ) -> ModificationResult:
        validation = ValidationResult(
            issues=[
                ValidationIssue(
                    path="patch",
                    check=check,
                    message=message,
                )
            ],
            checks_run=[check],
        )
        return ModificationResult(
            request=request,
            patch=CodePatch(
                summary=summary,
                changes=[],
                modification_reason=request.user_request,
                request_id=request.request_id,
                approval_required=False,
            ),
            validation_report=validation.format_report(),
            validation_ok=False,
            applied=False,
            approval_required=False,
            message=message,
        )

    def _operations_by_path(self, patch_set: PatchSet):
        operations: dict[str, list] = {}
        for operation in patch_set.operations:
            operations.setdefault(operation.path, []).append(operation)
        return operations

    def _change_type(self, old_content: str | None, new_content: str | None) -> str:
        if old_content is None and new_content is not None:
            return "create"
        if old_content is not None and new_content is None:
            return "delete"
        return "modify"
