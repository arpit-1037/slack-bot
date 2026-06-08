"""Deterministically select controlled workflows for repository tasks."""

from __future__ import annotations

from src.workflows.workflow_models import WorkflowSelection, WorkflowType


class WorkflowSelector:
    """Choose the most appropriate predefined workflow for a task."""

    def select_workflow(self, task: str) -> WorkflowSelection:
        """Return the selected workflow type and confidence."""
        text = " ".join(task.lower().split())
        scored: list[tuple[WorkflowType, float, list[str], str]] = [
            ("test_failure_investigation", self._score(text, ("test", "tests", "pytest", "unittest", "failing", "failed")), ["test or validation signals"], "Test Failure Investigation Workflow"),
            ("authentication_analysis", self._score(text, ("auth", "authentication", "jwt", "login", "middleware", "permission")), ["authentication signals"], "Authentication Analysis Workflow"),
            ("git_analysis", self._score(text, ("git", "commit", "branch", "diff", "status", "recent", "changed", "changes", "history")), ["git/history signals"], "Git Analysis Workflow"),
            ("architecture_analysis", self._score(text, ("architecture", "design", "overview", "modules", "system", "structure")), ["architecture signals"], "Architecture Analysis Workflow"),
            ("dependency_investigation", self._score(text, ("dependency", "dependencies", "dependents", "imports", "uses", "coupling")), ["dependency signals"], "Dependency Investigation Workflow"),
            ("performance_investigation", self._score(text, ("slow", "performance", "latency", "timeout", "cache", "bottleneck")), ["performance signals"], "Performance Investigation Workflow"),
            ("bug_investigation", self._score(text, ("investigate", "bug", "duplicate", "issue", "error", "failing", "broken", "root cause", "why")), ["bug/investigation signals"], "Bug Investigation Workflow"),
            ("repository_exploration", self._score(text, ("where", "which file", "which module", "find", "locate", "explain", "show")), ["repository lookup signals"], "Repository Exploration Workflow"),
        ]
        scored.sort(key=lambda item: (-item[1], item[3]))
        workflow_type, confidence, reasons, name = scored[0]
        if confidence <= 0.35:
            workflow_type = "repository_exploration"
            name = "Repository Exploration Workflow"
            confidence = 0.55
            reasons = ["default repository exploration"]
        return WorkflowSelection(
            workflow_type=workflow_type,
            workflow_name=name,
            confidence=round(confidence, 4),
            reasons=reasons,
        )

    def _score(self, text: str, signals: tuple[str, ...]) -> float:
        matches = [signal for signal in signals if signal in text]
        if not matches:
            return 0.0
        base = 0.55 + min(len(matches), 4) * 0.1
        if any(signal in text for signal in ("investigate", "analyze", "analyse", "explain", "review")):
            base += 0.05
        return min(base, 0.96)


_default_selector: WorkflowSelector | None = None


def default_workflow_selector() -> WorkflowSelector:
    """Return a lazily created workflow selector."""
    global _default_selector
    if _default_selector is None:
        _default_selector = WorkflowSelector()
    return _default_selector


def select_workflow(task: str) -> WorkflowSelection:
    """Select a workflow using the default selector."""
    return default_workflow_selector().select_workflow(task)
