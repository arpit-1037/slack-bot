"""Targeted patch generation and application utilities."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from src.llm.provider_router import ProviderRouter
from src.modification.modification_models import CodePatch, ModificationRequest
from src.repository.context_selector import ContextSelection
from src.utils.helpers import get_logger

log = get_logger(__name__)


class PatchGenerationError(RuntimeError):
    """Raised when an LLM patch response cannot be used safely."""


class PatchApplicationError(RuntimeError):
    """Raised when structured patch operations cannot be applied."""


@dataclass(frozen=True)
class PatchOperation:
    """One minimal structured edit operation."""

    op: str
    path: str
    target_type: str = "exact"
    target: str = ""
    content: str = ""
    position: str = "after"
    line_start: int | None = None
    line_end: int | None = None
    occurrence: int = 1
    reason: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PatchOperation":
        """Build and validate an operation from provider JSON."""
        op = str(data.get("op", "")).strip().lower()
        path = str(data.get("path", "")).strip()
        target_type = str(data.get("target_type", "exact")).strip().lower()
        position = str(data.get("position", "after")).strip().lower()

        if op not in {"insert", "replace", "delete"}:
            raise PatchGenerationError(f"Unsupported patch operation: {op}")
        if not path:
            raise PatchGenerationError("Patch operation missing path.")
        if target_type not in {"exact", "lines", "function", "class", "file", "eof", "bof"}:
            raise PatchGenerationError(f"Unsupported target_type: {target_type}")
        if position not in {"before", "after", "replace"}:
            raise PatchGenerationError(f"Unsupported insert position: {position}")

        return cls(
            op=op,
            path=path,
            target_type=target_type,
            target=str(data.get("target", "")),
            content=str(data.get("content", "")),
            position=position,
            line_start=_optional_int(data.get("line_start")),
            line_end=_optional_int(data.get("line_end")),
            occurrence=max(_optional_int(data.get("occurrence")) or 1, 1),
            reason=str(data.get("reason", "")),
        )


@dataclass(frozen=True)
class PatchSet:
    """A provider-generated set of structured patch operations."""

    summary: str
    operations: list[PatchOperation] = field(default_factory=list)
    risk_notes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PatchSet":
        """Build a patch set from provider JSON."""
        operations = [
            PatchOperation.from_dict(item)
            for item in data.get("operations", [])
            if isinstance(item, Mapping)
        ]
        return cls(
            summary=str(data.get("summary", "")).strip() or "Repository modification",
            operations=operations,
            risk_notes=[str(item) for item in data.get("risk_notes", [])],
        )

    @property
    def paths(self) -> list[str]:
        """Return affected repository paths in stable order."""
        return sorted({operation.path for operation in self.operations})


class PatchGenerator:
    """Ask an LLM for minimal structured edits and apply those edits in memory."""

    def __init__(self, provider_router: ProviderRouter | None = None) -> None:
        self.provider_router = provider_router or ProviderRouter()

    def generate_patch(
        self,
        request: ModificationRequest,
        request_id: str | None = None,
    ) -> CodePatch:
        """Generate a strongly typed code patch from retrieved repository context."""
        active_request_id = request_id or request.request_id
        raw_response = self.provider_router.complete(
            self._build_code_patch_messages(request),
            request_id=active_request_id,
        )
        patch_data = self._parse_patch_json(raw_response)
        patch = CodePatch.from_dict(
            patch_data,
            request_id=active_request_id,
            default_reason=request.user_request,
        )
        issues = self.validate_patch_structure(patch)
        if issues:
            raise PatchGenerationError("Invalid code patch: " + "; ".join(issues))
        log.info(
            "request_id=%s generated code patch changes=%d paths=%s",
            active_request_id,
            len(patch.changes),
            ",".join(patch.affected_paths),
        )
        return patch

    def validate_patch_structure(self, patch: CodePatch | Mapping[str, Any]) -> list[str]:
        """Return structural issues for a generated code patch."""
        if isinstance(patch, Mapping):
            patch = CodePatch.from_dict(patch)

        issues = []
        if not patch.summary.strip():
            issues.append("patch summary is required")
        for index, change in enumerate(patch.changes, start=1):
            label = f"change {index}"
            if not change.file_path:
                issues.append(f"{label} missing file_path")
            if change.old_content is None and change.new_content is None:
                issues.append(f"{label} has no old_content or new_content")
            if change.old_content == change.new_content:
                issues.append(f"{label} does not change file content")
            if change.file_path.startswith("/") or ".." in change.file_path.replace("\\", "/").split("/"):
                issues.append(f"{label} path must be repository-relative")
        return issues

    def generate(
        self,
        task: str,
        project_path: str,
        context_selection: ContextSelection,
        debug_context: str | None = None,
        request_id: str | None = None,
    ) -> PatchSet:
        """Generate minimal structured patch operations for a repository task."""
        messages = self._build_messages(task, project_path, context_selection, debug_context)
        raw_response = self.provider_router.complete(messages, request_id=request_id)
        patch_data = self._parse_patch_json(raw_response)
        patch_set = PatchSet.from_dict(patch_data)
        log.info(
            "request_id=%s generated patch operations=%d paths=%s",
            request_id,
            len(patch_set.operations),
            ",".join(patch_set.paths),
        )
        return patch_set

    def apply_operations(
        self,
        originals: Mapping[str, str | None],
        operations: list[PatchOperation],
    ) -> dict[str, str | None]:
        """Apply structured operations to in-memory file contents."""
        proposed: dict[str, str | None] = dict(originals)
        for operation in operations:
            current = proposed.get(operation.path)
            proposed[operation.path] = self._apply_operation(current, operation)
        return proposed

    def _apply_operation(self, current: str | None, operation: PatchOperation) -> str | None:
        if operation.op == "replace":
            return self._replace(current, operation)
        if operation.op == "insert":
            return self._insert(current, operation)
        if operation.op == "delete":
            return self._delete(current, operation)
        raise PatchApplicationError(f"Unsupported operation: {operation.op}")

    def _replace(self, current: str | None, operation: PatchOperation) -> str:
        if operation.target_type == "file":
            return operation.content
        content = self._require_existing_content(current, operation)
        if operation.target_type == "exact":
            return self._replace_exact(content, operation)
        if operation.target_type == "lines":
            return self._replace_lines(content, operation, operation.content)
        if operation.target_type in {"function", "class"}:
            start, end = self._symbol_range(content, operation)
            return self._replace_range(content, start, end, operation.content)
        raise PatchApplicationError(f"replace does not support target_type={operation.target_type}")

    def _insert(self, current: str | None, operation: PatchOperation) -> str:
        if current is None:
            if operation.target_type not in {"file", "eof", "bof"}:
                raise PatchApplicationError(
                    f"Cannot insert into missing file {operation.path} using target_type={operation.target_type}"
                )
            return operation.content

        if operation.target_type == "bof":
            return operation.content + current
        if operation.target_type in {"file", "eof"}:
            separator = "" if not current or current.endswith(("\n", "\r")) else "\n"
            return current + separator + operation.content
        if operation.target_type == "exact":
            return self._insert_exact(current, operation)
        if operation.target_type == "lines":
            line = operation.line_start or operation.line_end
            if line is None:
                raise PatchApplicationError(f"Line insert for {operation.path} missing line_start.")
            return self._insert_at_line(current, line, operation.content, operation.position)
        if operation.target_type in {"function", "class"}:
            start, end = self._symbol_range(current, operation)
            line = start if operation.position == "before" else end + 1
            return self._insert_at_line(current, line, operation.content, "before")
        raise PatchApplicationError(f"insert does not support target_type={operation.target_type}")

    def _delete(self, current: str | None, operation: PatchOperation) -> str | None:
        content = self._require_existing_content(current, operation)
        if operation.target_type == "file":
            return None
        if operation.target_type == "exact":
            return self._replace_exact(content, operation, replacement="")
        if operation.target_type == "lines":
            return self._replace_lines(content, operation, "")
        if operation.target_type in {"function", "class"}:
            start, end = self._symbol_range(content, operation)
            return self._replace_range(content, start, end, "")
        raise PatchApplicationError(f"delete does not support target_type={operation.target_type}")

    def _replace_exact(
        self,
        content: str,
        operation: PatchOperation,
        replacement: str | None = None,
    ) -> str:
        target = operation.target
        if not target:
            raise PatchApplicationError(f"Exact replacement for {operation.path} missing target.")
        index = self._find_occurrence(content, target, operation.occurrence)
        if index < 0:
            raise PatchApplicationError(f"Could not find exact target in {operation.path}.")
        new_text = operation.content if replacement is None else replacement
        return content[:index] + new_text + content[index + len(target):]

    def _insert_exact(self, content: str, operation: PatchOperation) -> str:
        target = operation.target
        if not target:
            raise PatchApplicationError(f"Exact insertion for {operation.path} missing target.")
        index = self._find_occurrence(content, target, operation.occurrence)
        if index < 0:
            raise PatchApplicationError(f"Could not find exact insert target in {operation.path}.")
        if operation.position == "before":
            return content[:index] + operation.content + content[index:]
        return content[:index + len(target)] + operation.content + content[index + len(target):]

    def _replace_lines(self, content: str, operation: PatchOperation, replacement: str) -> str:
        start = operation.line_start
        end = operation.line_end or operation.line_start
        if start is None or end is None:
            raise PatchApplicationError(f"Line replacement for {operation.path} missing line range.")
        return self._replace_range(content, start, end, replacement)

    def _replace_range(self, content: str, line_start: int, line_end: int, replacement: str) -> str:
        lines = content.splitlines(keepends=True)
        if line_start < 1 or line_end < line_start or line_end > max(len(lines), 1):
            raise PatchApplicationError(f"Invalid line range {line_start}-{line_end}.")
        replacement_lines = self._line_block(replacement, self._default_newline(content))
        return "".join(lines[: line_start - 1] + replacement_lines + lines[line_end:])

    def _insert_at_line(self, content: str, line: int, insertion: str, position: str) -> str:
        lines = content.splitlines(keepends=True)
        if line < 1 or line > len(lines) + 1:
            raise PatchApplicationError(f"Invalid insertion line {line}.")
        index = line - 1 if position == "before" else line
        insertion_lines = self._line_block(insertion, self._default_newline(content))
        return "".join(lines[:index] + insertion_lines + lines[index:])

    def _symbol_range(self, content: str, operation: PatchOperation) -> tuple[int, int]:
        try:
            tree = ast.parse(content, filename=operation.path)
        except SyntaxError as error:
            raise PatchApplicationError(f"Cannot locate symbols in unparsable file {operation.path}: {error}") from error

        target = operation.target.strip()
        if not target:
            raise PatchApplicationError(f"Symbol operation for {operation.path} missing target.")

        node = self._find_symbol(tree, target, operation.target_type)
        if node is None:
            raise PatchApplicationError(f"Could not find {operation.target_type} {target} in {operation.path}.")
        end = getattr(node, "end_lineno", None)
        if end is None:
            raise PatchApplicationError(f"Could not determine end line for {target} in {operation.path}.")
        return node.lineno, end

    def _find_symbol(
        self,
        tree: ast.AST,
        target: str,
        target_type: str,
    ) -> ast.AST | None:
        if "." in target:
            class_name, method_name = target.split(".", 1)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == class_name:
                    for child in node.body:
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == method_name:
                            return child
            return None

        for node in getattr(tree, "body", []):
            if target_type == "function" and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == target:
                    return node
            if target_type == "class" and isinstance(node, ast.ClassDef) and node.name == target:
                return node

        if target_type == "function":
            matches = [
                node for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == target
            ]
            if len(matches) == 1:
                return matches[0]
        return None

    def _build_messages(
        self,
        task: str,
        project_path: str,
        context_selection: ContextSelection,
        debug_context: str | None,
    ) -> list[dict]:
        """Build the provider prompt for structured patch operation generation."""
        debug_block = f"\n\nDEBUGGING CONTEXT:\n{debug_context}" if debug_context else ""
        return [
            {
                "role": "system",
                "content": (
                    "You generate safe, minimal repository patch operations. "
                    "Return JSON only. Do not include markdown, prose, or full-file rewrites unless unavoidable."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Create targeted patch operations for this repository task.\n\n"
                    f"PROJECT PATH: {project_path}\n"
                    f"TASK:\n{task}\n\n"
                    "Rules:\n"
                    "- Modify only relevant code blocks.\n"
                    "- Preserve unrelated code and formatting.\n"
                    "- Prefer target_type=function, class, lines, or exact over target_type=file.\n"
                    "- For function/class replacement, content must include the complete replacement block.\n"
                    "- Use repository-relative paths.\n"
                    "- If the task is unsafe or too ambiguous, return an empty operations array and explain in risk_notes.\n\n"
                    "JSON schema:\n"
                    "{\n"
                    '  "summary": "short summary",\n'
                    '  "risk_notes": ["risk or ambiguity"],\n'
                    '  "operations": [\n'
                    "    {\n"
                    '      "op": "insert|replace|delete",\n'
                    '      "path": "repo/relative/file.py",\n'
                    '      "target_type": "exact|lines|function|class|file|eof|bof",\n'
                    '      "target": "exact text or symbol name",\n'
                    '      "content": "replacement or inserted text",\n'
                    '      "position": "before|after|replace",\n'
                    '      "line_start": 1,\n'
                    '      "line_end": 2,\n'
                    '      "occurrence": 1,\n'
                    '      "reason": "why this minimal edit is needed"\n'
                    "    }\n"
                    "  ]\n"
                    "}\n\n"
                    f"REPOSITORY CONTEXT:\n{context_selection.context}"
                    f"{debug_block}"
                ),
            },
        ]

    def _build_code_patch_messages(self, request: ModificationRequest) -> list[dict]:
        """Build a prompt for direct CodePatch JSON generation."""
        selected_files = ", ".join(request.selected_files) or "none supplied"
        return [
            {
                "role": "system",
                "content": (
                    "You generate safe code patch previews. Return JSON only. "
                    "Do not apply changes, run commands, commit, push, or create pull requests."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Create a reviewable code patch for this repository request.\n\n"
                    f"REQUEST:\n{request.user_request}\n\n"
                    f"SELECTED FILES:\n{selected_files}\n\n"
                    "Rules:\n"
                    "- Use only repository-relative file paths.\n"
                    "- Include old_content and new_content for each changed file.\n"
                    "- Set old_content to null for created files.\n"
                    "- Set new_content to null only for deleted files.\n"
                    "- Keep changes minimal and explain each change.\n"
                    "- If the request is unsafe or ambiguous, return an empty changes array and explain why.\n\n"
                    "JSON schema:\n"
                    "{\n"
                    '  "summary": "short summary",\n'
                    '  "diff_summary": "human readable summary",\n'
                    '  "modification_reason": "why this patch is needed",\n'
                    '  "approval_required": true,\n'
                    '  "changes": [\n'
                    "    {\n"
                    '      "file_path": "repo/relative/file.py",\n'
                    '      "old_content": "original file content or block",\n'
                    '      "new_content": "new file content or block",\n'
                    '      "diff_summary": "what changed in this file",\n'
                    '      "modification_reason": "why this file changed",\n'
                    '      "change_type": "create|modify|delete"\n'
                    "    }\n"
                    "  ]\n"
                    "}\n\n"
                    f"RETRIEVED REPOSITORY CONTEXT:\n{request.repository_context}"
                ),
            },
        ]

    def _parse_patch_json(self, response: str) -> dict[str, Any]:
        """Extract and parse provider JSON."""
        cleaned = response.strip()
        fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            cleaned = fenced.group(1).strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as error:
            raise PatchGenerationError("Provider did not return valid patch JSON.") from error
        if not isinstance(data, dict):
            raise PatchGenerationError("Patch response JSON must be an object.")
        return data

    def _find_occurrence(self, content: str, target: str, occurrence: int) -> int:
        index = -1
        start = 0
        for _ in range(occurrence):
            index = content.find(target, start)
            if index < 0:
                return -1
            start = index + len(target)
        return index

    def _line_block(self, content: str, newline: str) -> list[str]:
        if not content:
            return []
        if not content.endswith(("\n", "\r")):
            content += newline
        return content.splitlines(keepends=True)

    def _default_newline(self, content: str) -> str:
        if "\r\n" in content:
            return "\r\n"
        if "\r" in content:
            return "\r"
        return "\n"

    def _require_existing_content(self, current: str | None, operation: PatchOperation) -> str:
        if current is None:
            raise PatchApplicationError(f"Operation {operation.op} requires existing file {operation.path}.")
        return current


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
