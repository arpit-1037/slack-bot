"""Unified execution layer for assistant tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from src.tools.base_tool import ToolResult, ToolValidationError
from src.tools.tool_registry import ToolRegistry, create_default_registry


@dataclass(frozen=True)
class ToolRequest:
    """One normalized tool execution request."""

    name: str
    input: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: "ToolRequest | Mapping[str, Any]") -> "ToolRequest":
        """Build a request from a dataclass or dictionary payload."""
        if isinstance(payload, ToolRequest):
            return payload
        if not isinstance(payload, Mapping):
            raise ValueError("Tool request must be a mapping or ToolRequest.")
        name = str(payload.get("name") or payload.get("tool") or "").strip()
        if not name:
            raise ValueError("Tool request is missing a tool name.")
        tool_input = payload.get("input", payload.get("tool_input", {}))
        if tool_input is None:
            tool_input = {}
        if not isinstance(tool_input, Mapping):
            raise ValueError("Tool request input must be a mapping.")
        metadata = payload.get("metadata", {})
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, Mapping):
            raise ValueError("Tool request metadata must be a mapping.")
        return cls(name=name, input=dict(tool_input), metadata=dict(metadata))


class ToolExecutor:
    """Execute registered tools with standardized failure handling."""

    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self.registry = registry or create_default_registry()

    def execute_tool(
        self,
        name: str,
        tool_input: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        """Execute one named tool through the registry."""
        normalized_name = str(name or "").strip()
        if not normalized_name:
            return ToolResult.failure_result(
                tool_name="unknown",
                error="Tool name is required.",
                validation_errors=[
                    ToolValidationError("name", "Tool name is required.")
                ],
            )

        tool = self.registry.get_tool(normalized_name)
        if tool is None:
            return ToolResult.failure_result(
                tool_name=normalized_name,
                error=f"Tool not registered: {normalized_name}",
            )

        return tool.execute(tool_input)

    def execute_tools(
        self,
        requests: list[ToolRequest | Mapping[str, Any]],
    ) -> list[ToolResult]:
        """Execute multiple tool requests and collect standardized results."""
        results: list[ToolResult] = []
        for raw_request in requests:
            try:
                request = ToolRequest.from_payload(raw_request)
            except Exception as error:
                results.append(
                    ToolResult.failure_result(
                        tool_name="unknown",
                        error=str(error),
                    )
                )
                continue
            results.append(self.execute_tool(request.name, request.input))
        return results
