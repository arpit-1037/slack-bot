"""Base abstractions for structured assistant tools."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class ToolMetadata:
    """Descriptive metadata used for discovery and execution planning."""

    name: str
    description: str
    category: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    read_only: bool = True

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe metadata dictionary."""
        return _jsonable(asdict(self))


@dataclass(frozen=True)
class ToolValidationError:
    """One validation error for a tool request."""

    field: str
    message: str

    def as_dict(self) -> dict[str, str]:
        """Return a structured validation error."""
        return {"field": self.field, "message": self.message}


@dataclass(frozen=True)
class ToolResult:
    """Standard result envelope returned by every tool."""

    tool_name: str
    success: bool
    status: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    validation_errors: list[ToolValidationError] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success_result(
        cls,
        tool_name: str,
        data: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "ToolResult":
        """Build a successful result envelope."""
        return cls(
            tool_name=tool_name,
            success=True,
            status="success",
            data=_jsonable(dict(data or {})),
            metadata=_jsonable(dict(metadata or {})),
        )

    @classmethod
    def failure_result(
        cls,
        tool_name: str,
        error: str,
        validation_errors: list[ToolValidationError] | None = None,
        data: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "ToolResult":
        """Build a failed result envelope."""
        return cls(
            tool_name=tool_name,
            success=False,
            status="failure",
            data=_jsonable(dict(data or {})),
            error=error,
            validation_errors=validation_errors or [],
            metadata=_jsonable(dict(metadata or {})),
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe result dictionary."""
        return {
            "tool_name": self.tool_name,
            "success": self.success,
            "status": self.status,
            "data": _jsonable(self.data),
            "error": self.error,
            "validation_errors": [
                validation_error.as_dict()
                for validation_error in self.validation_errors
            ],
            "metadata": _jsonable(self.metadata),
        }


class BaseTool(ABC):
    """Common execution contract for all assistant tools."""

    metadata: ToolMetadata

    def get_metadata(self) -> dict[str, Any]:
        """Return structured tool metadata for discovery."""
        return self.metadata.as_dict()

    def validate_input(self, tool_input: Mapping[str, Any]) -> list[ToolValidationError]:
        """Validate a tool input payload before execution."""
        return []

    def execute(self, tool_input: Mapping[str, Any] | None = None) -> ToolResult:
        """Validate and execute a tool request."""
        if tool_input is None:
            payload: dict[str, Any] = {}
        elif isinstance(tool_input, Mapping):
            payload = dict(tool_input)
        else:
            return self._failure(
                "Tool input must be a mapping.",
                validation_errors=[
                    ToolValidationError("input", "Tool input must be a mapping.")
                ],
            )

        validation_errors = self.validate_input(payload)
        if validation_errors:
            return self._failure(
                "Tool input validation failed.",
                validation_errors=validation_errors,
            )

        try:
            return self._execute(payload)
        except Exception as error:
            return self._failure(str(error))

    @abstractmethod
    def _execute(self, tool_input: Mapping[str, Any]) -> ToolResult:
        """Run the tool against a validated input payload."""

    def _success(
        self,
        data: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        """Return a successful result for this tool."""
        return ToolResult.success_result(
            tool_name=self.metadata.name,
            data=data,
            metadata=metadata,
        )

    def _failure(
        self,
        error: str,
        validation_errors: list[ToolValidationError] | None = None,
        data: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        """Return a failed result for this tool."""
        return ToolResult.failure_result(
            tool_name=self.metadata.name,
            error=error,
            validation_errors=validation_errors,
            data=data,
            metadata=metadata,
        )


def _jsonable(value: Any) -> Any:
    """Convert common Python objects into JSON-safe structures."""
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value
