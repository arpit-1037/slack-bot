"""Strongly typed models for thinking-only repository planning."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

TaskTypeName = Literal[
    "Bug Fix",
    "Refactor",
    "Feature Development",
    "Investigation",
    "Documentation",
    "Git Task",
    "Testing",
    "Configuration Change",
    "Repository Exploration",
]
ComplexityName = Literal["Trivial", "Small", "Medium", "Large", "Very Large"]
RiskLevelName = Literal["Low", "Medium", "High"]

TASK_TYPES: tuple[TaskTypeName, ...] = (
    "Bug Fix",
    "Refactor",
    "Feature Development",
    "Investigation",
    "Documentation",
    "Git Task",
    "Testing",
    "Configuration Change",
    "Repository Exploration",
)
COMPLEXITY_LEVELS: tuple[ComplexityName, ...] = ("Trivial", "Small", "Medium", "Large", "Very Large")
RISK_LEVELS: tuple[RiskLevelName, ...] = ("Low", "Medium", "High")


@dataclass(frozen=True)
class PlanningFileContext:
    """Repository file selected as relevant to a plan."""

    path: str
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PlanningSymbolContext:
    """Repository symbol selected as relevant to a plan."""

    name: str
    kind: str
    file_path: str
    line_start: int = 0
    line_end: int = 0
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GitPlanningContext:
    """Read-only git state that can inform a plan."""

    branch: str = ""
    head_commit: str = ""
    changed_files: list[str] = field(default_factory=list)
    staged_files: list[str] = field(default_factory=list)
    untracked_files: list[str] = field(default_factory=list)
    recent_commits: list[str] = field(default_factory=list)
    recent_changed_files: list[str] = field(default_factory=list)

    @property
    def has_context(self) -> bool:
        """Return True when any git signal is available."""
        return bool(
            self.branch
            or self.head_commit
            or self.changed_files
            or self.staged_files
            or self.untracked_files
            or self.recent_commits
            or self.recent_changed_files
        )


@dataclass(frozen=True)
class PlanningContext:
    """Repository, git, and debugging context used to generate a plan."""

    task: str
    project_path: str = ""
    repository_files: list[PlanningFileContext] = field(default_factory=list)
    repository_symbols: list[PlanningSymbolContext] = field(default_factory=list)
    retrieval_explanations: list[str] = field(default_factory=list)
    repository_summary: dict[str, Any] = field(default_factory=dict)
    git: GitPlanningContext = field(default_factory=GitPlanningContext)
    debug_notes: list[str] = field(default_factory=list)

    @property
    def has_repository_context(self) -> bool:
        """Return True when repository retrieval found useful context."""
        return bool(self.repository_files or self.repository_symbols or self.repository_summary)


@dataclass(frozen=True)
class TaskAnalysis:
    """Classification and planning needs for one user task."""

    task_type: TaskTypeName
    complexity: ComplexityName
    requires_repository_context: bool
    requires_git_context: bool
    confidence: float = 0.0
    signals: list[str] = field(default_factory=list)
    reasoning: str = ""
    safety_notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PlanStep:
    """One ordered step in a generated implementation plan."""

    id: str
    title: str
    description: str
    dependencies: list[str]
    risk_level: RiskLevelName
    expected_outcome: str


@dataclass(frozen=True)
class PlanValidationResult:
    """Validation result for a generated plan."""

    valid: bool
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Plan:
    """Structured plan returned by the planning engine without executing it."""

    goal: str
    analysis: TaskAnalysis
    context: PlanningContext
    steps: list[PlanStep]
    validation: PlanValidationResult = field(
        default_factory=lambda: PlanValidationResult(valid=True)
    )
    explanation: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary representation of the plan."""
        return asdict(self)

    def format_markdown(self) -> str:
        """Return a Slack-friendly structured plan response."""
        lines = [
            "*Planning Engine*",
            "No code, git, or filesystem actions were executed.",
            "",
            f"*Goal:* {self.goal}",
            f"*Type:* {self.analysis.task_type}",
            f"*Complexity:* {self.analysis.complexity}",
        ]

        if self.context.repository_files:
            lines.extend(["", "*Repository Context:*"])
            for file_context in self.context.repository_files[:6]:
                reasons = ", ".join(file_context.reasons[:3]) or "selected"
                lines.append(f"- {file_context.path} ({reasons})")

        if self.context.repository_symbols:
            lines.extend(["", "*Relevant Symbols:*"])
            for symbol in self.context.repository_symbols[:6]:
                lines.append(
                    f"- {symbol.name} ({symbol.kind}) in {symbol.file_path}:{symbol.line_start}-{symbol.line_end}"
                )

        if self.context.git.has_context:
            head = self.context.git.head_commit[:12] if self.context.git.head_commit else "unavailable"
            lines.extend(
                [
                    "",
                    "*Git Context:*",
                    f"- branch: {self.context.git.branch or 'unknown'}",
                    f"- head: {head}",
                    f"- changed files: {', '.join(self.context.git.changed_files) or 'none'}",
                    f"- staged files: {', '.join(self.context.git.staged_files) or 'none'}",
                    f"- untracked files: {', '.join(self.context.git.untracked_files) or 'none'}",
                ]
            )
            if self.context.git.recent_changed_files:
                lines.append(
                    "- recent changed files: "
                    + ", ".join(self.context.git.recent_changed_files[:8])
                )

        lines.extend(["", "*Plan:*"])
        for index, step in enumerate(self.steps, start=1):
            dependencies = ", ".join(step.dependencies) or "none"
            lines.extend(
                [
                    f"{index}. *{step.title}*",
                    f"   Description: {step.description}",
                    f"   Depends on: {dependencies}",
                    f"   Risk: {step.risk_level}",
                    f"   Expected outcome: {step.expected_outcome}",
                ]
            )

        lines.extend(["", f"*Validation:* {'valid' if self.validation.valid else 'invalid'}"])
        if self.validation.errors:
            lines.append("Errors:")
            lines.extend(f"- {error}" for error in self.validation.errors)
        if self.validation.warnings:
            lines.append("Warnings:")
            lines.extend(f"- {warning}" for warning in self.validation.warnings)

        if self.explanation:
            lines.extend(["", "*Why These Steps:*"])
            lines.extend(f"- {item}" for item in self.explanation)

        return "\n".join(lines)
