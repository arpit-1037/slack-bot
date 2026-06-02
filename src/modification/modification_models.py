"""Typed models for safe code modification workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class PatchChange:
    """One proposed file content change."""

    file_path: str
    old_content: str | None
    new_content: str | None
    diff_summary: str = ""
    modification_reason: str = ""
    change_type: str = "modify"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_creation(self) -> bool:
        """Return True when the change creates a new file."""
        return self.old_content is None and self.new_content is not None

    @property
    def is_deletion(self) -> bool:
        """Return True when the change deletes an existing file."""
        return self.old_content is not None and self.new_content is None

    @property
    def is_modified(self) -> bool:
        """Return True when the change updates an existing file."""
        return self.old_content is not None and self.new_content is not None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PatchChange":
        """Build a patch change from provider or test data."""
        file_path = str(data.get("file_path") or data.get("path") or "").strip()
        return cls(
            file_path=file_path,
            old_content=_optional_text(data.get("old_content")),
            new_content=_optional_text(data.get("new_content")),
            diff_summary=str(data.get("diff_summary", "")).strip(),
            modification_reason=str(data.get("modification_reason") or data.get("reason") or "").strip(),
            change_type=str(data.get("change_type", "modify")).strip().lower() or "modify",
            metadata=dict(data.get("metadata", {})) if isinstance(data.get("metadata"), Mapping) else {},
        )


@dataclass(frozen=True)
class CodePatch:
    """A complete proposed code patch awaiting review or approval."""

    summary: str
    changes: list[PatchChange] = field(default_factory=list)
    diff_summary: str = ""
    modification_reason: str = ""
    request_id: str | None = None
    approval_required: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def affected_paths(self) -> list[str]:
        """Return affected repository paths in stable order."""
        return sorted({change.file_path for change in self.changes})

    @property
    def has_changes(self) -> bool:
        """Return True when at least one effective file change is present."""
        return any(change.old_content != change.new_content for change in self.changes)

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        request_id: str | None = None,
        default_reason: str = "",
    ) -> "CodePatch":
        """Build a code patch from a structured mapping."""
        changes = [
            PatchChange.from_dict(item)
            for item in data.get("changes", [])
            if isinstance(item, Mapping)
        ]
        return cls(
            summary=str(data.get("summary", "")).strip() or "Repository modification",
            changes=changes,
            diff_summary=str(data.get("diff_summary", "")).strip(),
            modification_reason=str(data.get("modification_reason", "")).strip() or default_reason,
            request_id=request_id or _optional_text(data.get("request_id")),
            approval_required=bool(data.get("approval_required", True)),
            metadata=dict(data.get("metadata", {})) if isinstance(data.get("metadata"), Mapping) else {},
        )


@dataclass(frozen=True)
class ModificationRequest:
    """Input for a controlled code modification preview."""

    user_request: str
    repository_context: str = ""
    selected_files: list[str] = field(default_factory=list)
    project_path: str = ""
    request_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SafetyIssue:
    """One safety finding for a proposed modification."""

    file_path: str
    reason: str
    severity: str = "error"
    requires_approval: bool = False

    def format(self) -> str:
        """Format a concise safety issue line."""
        approval = " approval-required" if self.requires_approval else ""
        return f"[{self.severity}]{approval} {self.file_path}: {self.reason}"


@dataclass(frozen=True)
class ModificationResult:
    """Result of a preview or approved code modification operation."""

    request: ModificationRequest
    patch: CodePatch
    unified_diff: str = ""
    file_diffs: dict[str, str] = field(default_factory=dict)
    change_summaries: list[str] = field(default_factory=list)
    safety_issues: list[SafetyIssue] = field(default_factory=list)
    validation_report: str = ""
    validation_ok: bool = True
    applied: bool = False
    approval_required: bool = True
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Return True when safety and validation did not find blocking errors."""
        return self.validation_ok and not any(issue.severity == "error" for issue in self.safety_issues)

    def format_response(self) -> str:
        """Format the result for Slack or CLI display."""
        status = "Applied" if self.applied else "Preview only"
        if self.approval_required and not self.applied:
            status += " - awaiting approval"

        lines = [
            f"*Code modification: {status}*",
            f"*Summary:* {self.patch.summary}",
        ]
        if self.message:
            lines.append(self.message)
        if self.patch.affected_paths:
            lines.append("*Files:* " + ", ".join(self.patch.affected_paths))
        if self.change_summaries:
            lines.append("*Change summary:*\n" + "\n".join(f"- {item}" for item in self.change_summaries))
        if self.safety_issues:
            lines.append("*Safety:*\n" + "\n".join(issue.format() for issue in self.safety_issues))
        if self.validation_report:
            lines.append("*Validation:* " + self.validation_report)
        if self.unified_diff:
            lines.append(f"```diff\n{self.unified_diff.rstrip()}\n```")
        return "\n\n".join(lines)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
