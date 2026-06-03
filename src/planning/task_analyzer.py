"""Deterministic task classification and complexity estimation."""

from __future__ import annotations

import re
from dataclasses import replace

from src.planning.execution_models import ComplexityName, PlanningContext, TaskAnalysis, TaskTypeName


class TaskAnalyzer:
    """Classify tasks and estimate planning complexity without LLM calls."""

    _signals_by_type: dict[TaskTypeName, tuple[str, ...]] = {
        "Bug Fix": (
            "fix", "bug", "error", "issue", "broken", "failing", "failed", "duplicate",
            "crash", "exception", "not working", "regression", "retry",
        ),
        "Refactor": (
            "refactor", "cleanup", "clean up", "restructure", "reorganize", "simplify",
            "extract", "rename", "decouple",
        ),
        "Feature Development": (
            "add", "implement", "support", "feature", "build", "create", "new flow",
            "integration", "middleware",
        ),
        "Investigation": (
            "investigate", "analyze", "why", "trace", "diagnose", "understand",
            "root cause", "inspect",
        ),
        "Documentation": (
            "document", "documentation", "readme", "docs", "guide", "explain",
            "changelog", "example",
        ),
        "Git Task": (
            "git", "commit", "branch", "merge", "rebase", "push", "pull", "stash",
            "diff", "status", "history",
        ),
        "Testing": (
            "test", "tests", "pytest", "unittest", "coverage", "regression test",
            "integration test", "verification",
        ),
        "Configuration Change": (
            "config", "configuration", "env", ".env", "setting", "settings", "variable",
            "secret", "model", "provider",
        ),
        "Repository Exploration": (
            "where is", "where are", "which file", "which files", "locate", "find",
            "show me", "related to", "handles", "uses", "depends on",
        ),
    }

    _planning_prefixes = (
        "create a plan for",
        "create an implementation plan for",
        "create a refactor plan for",
        "make a plan for",
        "generate a plan for",
        "give me a plan for",
        "give me an implementation plan for",
        "draft a plan for",
        "plan for",
        "how would you",
        "how should we",
        "how should i",
        "how to",
    )

    def analyze_task(self, task: str, context: PlanningContext | None = None) -> TaskAnalysis:
        """Return a structured analysis for a user task."""
        normalized = self._normalize_task(task)
        task_type, signals = self._classify(normalized)
        requires_repository_context = self._requires_repository_context(normalized, task_type)
        requires_git_context = self._requires_git_context(normalized, task_type)
        complexity = self.estimate_complexity(normalized, task_type=task_type, context=context)
        confidence = self._confidence(signals, task_type)
        safety_notes = [
            "Planning output only; no code, git, deployment, or filesystem actions are executed."
        ]
        reasoning = (
            f"Classified as {task_type} using signals: {', '.join(signals) or 'default repository task'}."
        )
        analysis = TaskAnalysis(
            task_type=task_type,
            complexity=complexity,
            requires_repository_context=requires_repository_context,
            requires_git_context=requires_git_context,
            confidence=confidence,
            signals=signals,
            reasoning=reasoning,
            safety_notes=safety_notes,
        )

        if context is not None and context.has_repository_context and not requires_repository_context:
            return replace(analysis, requires_repository_context=True)
        return analysis

    def estimate_complexity(
        self,
        task: str,
        task_type: TaskTypeName | None = None,
        context: PlanningContext | None = None,
    ) -> ComplexityName:
        """Estimate task complexity from scope, repository impact, dependencies, and tests."""
        task_lower = task.lower()
        task_type = task_type or self._classify(self._normalize_task(task))[0]
        score = 0

        if task_type in {"Bug Fix", "Feature Development", "Refactor", "Configuration Change"}:
            score += 2
        if task_type in {"Testing", "Documentation", "Repository Exploration"}:
            score += 1
        if task_type == "Git Task":
            score += 1

        if any(word in task_lower for word in ("auth", "jwt", "database", "migration", "slack", "api", "security")):
            score += 1
        if any(word in task_lower for word in ("duplicate", "retry", "regression", "failing", "failed", "not working")):
            score += 1
        if any(word in task_lower for word in ("many files", "across", "system-wide", "architecture", "major")):
            score += 2
        if any(word in task_lower for word in ("large", "very large", "complex", "hard")):
            score += 2
        if any(word in task_lower for word in ("test", "tests", "coverage", "integration")):
            score += 1

        explicit_files = re.findall(r"[\w./-]+\.(?:py|js|ts|php|json|md|yaml|yml)", task_lower)
        if len(set(explicit_files)) >= 2:
            score += 1

        if context is not None:
            file_count = len(context.repository_files)
            dependency_depth = max(
                (len(file.dependencies) + len(file.dependents) for file in context.repository_files),
                default=0,
            )
            if file_count >= 6:
                score += 2
            elif file_count >= 3:
                score += 1
            if dependency_depth >= 4:
                score += 2
            elif dependency_depth >= 2:
                score += 1
            if context.git.changed_files or context.git.staged_files:
                score += 1

        if score <= 1:
            return "Trivial"
        if score <= 3:
            return "Small"
        if score <= 5:
            return "Medium"
        if score <= 7:
            return "Large"
        return "Very Large"

    def _classify(self, task: str) -> tuple[TaskTypeName, list[str]]:
        """Classify the task from deterministic keyword signals."""
        task_lower = task.lower()
        order: tuple[TaskTypeName, ...] = (
            "Repository Exploration",
            "Bug Fix",
            "Refactor",
            "Testing",
            "Configuration Change",
            "Documentation",
            "Git Task",
            "Feature Development",
            "Investigation",
        )
        for task_type in order:
            matches = [signal for signal in self._signals_by_type[task_type] if signal in task_lower]
            if matches:
                return task_type, matches[:5]
        return "Investigation", []

    def _normalize_task(self, task: str) -> str:
        """Remove planning wrapper phrases before classification."""
        normalized = " ".join(task.split()).strip()
        lowered = normalized.lower()
        for prefix in self._planning_prefixes:
            if lowered.startswith(prefix):
                return normalized[len(prefix):].strip(" :.-") or normalized
        return normalized

    def _requires_repository_context(self, task: str, task_type: TaskTypeName) -> bool:
        """Return True when repository retrieval should inform the plan."""
        task_lower = task.lower()
        repository_signals = (
            "repo", "repository", "codebase", "file", "module", "class", "function",
            "handler", "controller", "service", "route", "slack", "jwt", "auth", "src/",
            ".py", ".js", ".ts", ".php",
        )
        return (
            task_type in {
                "Bug Fix",
                "Refactor",
                "Feature Development",
                "Testing",
                "Configuration Change",
                "Repository Exploration",
            }
            or any(signal in task_lower for signal in repository_signals)
        )

    def _requires_git_context(self, task: str, task_type: TaskTypeName) -> bool:
        """Return True when read-only git state should inform the plan."""
        task_lower = task.lower()
        git_signals = (
            "git", "commit", "branch", "diff", "status", "changed", "recent",
            "yesterday", "regression", "last", "history",
        )
        return task_type in {"Bug Fix", "Refactor", "Git Task"} or any(signal in task_lower for signal in git_signals)

    def _confidence(self, signals: list[str], task_type: TaskTypeName) -> float:
        """Return a bounded classification confidence score."""
        if not signals:
            return 0.45
        base = 0.65 + min(len(signals), 4) * 0.08
        if task_type in {"Bug Fix", "Repository Exploration"}:
            base += 0.05
        return min(base, 0.95)


_default_analyzer = TaskAnalyzer()


def analyze_task(task: str, context: PlanningContext | None = None) -> TaskAnalysis:
    """Analyze a task using the default deterministic analyzer."""
    return _default_analyzer.analyze_task(task, context=context)


def estimate_complexity(
    task: str,
    task_type: TaskTypeName | None = None,
    context: PlanningContext | None = None,
) -> ComplexityName:
    """Estimate task complexity using the default deterministic analyzer."""
    return _default_analyzer.estimate_complexity(task, task_type=task_type, context=context)
