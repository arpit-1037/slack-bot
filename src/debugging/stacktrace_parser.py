"""Stacktrace parsing for repository-aware debugging."""

from __future__ import annotations

import os
import re
import traceback
from dataclasses import dataclass, field

from src.utils.helpers import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class StackFrame:
    """One parsed stack frame."""

    filename: str
    line_number: int
    function_name: str


@dataclass(frozen=True)
class ParsedStackTrace:
    """Structured stacktrace information extracted from user text."""

    has_stacktrace: bool
    error_type: str | None = None
    error_message: str | None = None
    frames: list[StackFrame] = field(default_factory=list)

    @property
    def files(self) -> list[str]:
        """Return unique filenames mentioned in the trace."""
        return _unique(frame.filename for frame in self.frames)

    @property
    def lines(self) -> list[int]:
        """Return unique line numbers mentioned in the trace."""
        return _unique(frame.line_number for frame in self.frames)

    @property
    def functions(self) -> list[str]:
        """Return unique function names mentioned in the trace."""
        return _unique(frame.function_name for frame in self.frames)

    def as_dict(self) -> dict:
        """Return a dict-shaped result for tests and logging."""
        return {
            "error_type": self.error_type,
            "error_message": self.error_message,
            "files": self.files,
            "lines": self.lines,
            "functions": self.functions,
        }


class StacktraceParser:
    """Detect and parse Python stack traces from user input."""

    _python_frame_pattern = re.compile(
        r'^\s*File "([^"]+)", line (\d+), in ([^\s]+)\s*$'
    )
    _error_pattern = re.compile(
        r"^\s*([A-Za-z_][\w.]*(?:Error|Exception|Warning|Interrupt|Exit))(?::\s*(.*))?\s*$"
    )

    def detect(self, text: str) -> bool:
        """Return True when text appears to contain a stacktrace."""
        return (
            "Traceback (most recent call last):" in text
            or any(self._python_frame_pattern.match(line) for line in text.splitlines())
            or self._error_pattern.search(text.strip()) is not None
        )

    def parse(self, text: str) -> ParsedStackTrace:
        """Parse a Python stacktrace, tolerating malformed or partial traces."""
        frames: list[StackFrame] = []
        frame_summaries: list[traceback.FrameSummary] = []

        for line in text.splitlines():
            match = self._python_frame_pattern.match(line)
            if not match:
                continue

            filename = match.group(1)
            line_number = self._safe_int(match.group(2))
            function_name = match.group(3)
            if line_number is None:
                continue

            frames.append(StackFrame(filename=filename, line_number=line_number, function_name=function_name))
            frame_summaries.append(traceback.FrameSummary(filename, line_number, function_name))

        if frame_summaries:
            traceback.StackSummary.from_list(frame_summaries)

        error_type, error_message = self._parse_error_line(text)
        parsed = ParsedStackTrace(
            has_stacktrace=bool(frames) or self.detect(text),
            error_type=error_type,
            error_message=error_message,
            frames=frames,
        )
        log.info(
            "Parsed stacktrace has_stacktrace=%s frames=%d error_type=%s",
            parsed.has_stacktrace,
            len(parsed.frames),
            parsed.error_type,
        )
        return parsed

    def _parse_error_line(self, text: str) -> tuple[str | None, str | None]:
        """Find the most likely final Python error line."""
        for line in reversed([line.strip() for line in text.splitlines() if line.strip()]):
            if line.startswith(("File ", "Traceback ", "^", "~")):
                continue
            if os.path.exists(line):
                continue
            match = self._error_pattern.match(line)
            if match:
                return match.group(1), match.group(2) or None
        return None, None

    def _safe_int(self, value: str) -> int | None:
        """Parse an integer without raising."""
        try:
            return int(value)
        except ValueError:
            return None


def _unique(values) -> list:
    """Return values in original order without duplicates."""
    seen = set()
    ordered = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered
