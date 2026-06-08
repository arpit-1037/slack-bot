"""Predefined controlled analysis workflow templates."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.workflows.workflow_models import WorkflowStepKind, WorkflowType


@dataclass(frozen=True)
class WorkflowStepTemplate:
    """Reusable step template for workflow construction."""

    title: str
    task_template: str
    kind: WorkflowStepKind
    expected_outcome: str


@dataclass(frozen=True)
class WorkflowTemplate:
    """Reusable workflow template definition."""

    name: str
    workflow_type: WorkflowType
    description: str
    steps: tuple[WorkflowStepTemplate, ...]
    tags: tuple[str, ...] = field(default_factory=tuple)


PREDEFINED_WORKFLOWS: dict[WorkflowType, WorkflowTemplate] = {
    "bug_investigation": WorkflowTemplate(
        name="Bug Investigation Workflow",
        workflow_type="bug_investigation",
        description="Investigate repository bugs with evidence, git context, validation, and findings.",
        tags=("bug", "investigation", "root-cause"),
        steps=(
            WorkflowStepTemplate(
                "Repository Search",
                "Investigate {task}. Locate the affected files, symbols, and handlers.",
                "repository",
                "Relevant repository areas are identified.",
            ),
            WorkflowStepTemplate(
                "Git Analysis",
                "Analyze recent repository changes related to {task}.",
                "git",
                "Recent change context is known.",
            ),
            WorkflowStepTemplate(
                "Validation",
                "Check validation and tests related to {task}.",
                "validation",
                "Validation evidence is collected.",
            ),
            WorkflowStepTemplate(
                "Root Cause Report",
                "Summarize confirmed evidence and likely root cause for {task}.",
                "analysis",
                "A concise root-cause report is prepared.",
            ),
        ),
    ),
    "authentication_analysis": WorkflowTemplate(
        name="Authentication Analysis Workflow",
        workflow_type="authentication_analysis",
        description="Analyze authentication, login, JWT, middleware, and authorization flow.",
        tags=("auth", "authentication", "jwt", "login"),
        steps=(
            WorkflowStepTemplate(
                "Memory Check",
                "Use repository memory to locate authentication flow for {task}.",
                "memory",
                "Known authentication facts are reused.",
            ),
            WorkflowStepTemplate(
                "Symbol Search",
                "Find authentication symbols, handlers, middleware, and services for {task}.",
                "repository",
                "Authentication code locations are identified.",
            ),
            WorkflowStepTemplate(
                "Dependency Analysis",
                "Analyze dependencies and call flow around authentication for {task}.",
                "repository",
                "Authentication relationships are mapped.",
            ),
            WorkflowStepTemplate(
                "Architecture Summary",
                "Summarize the authentication architecture for {task}.",
                "analysis",
                "Authentication flow is explained with evidence.",
            ),
        ),
    ),
    "git_analysis": WorkflowTemplate(
        name="Git Analysis Workflow",
        workflow_type="git_analysis",
        description="Analyze repository status, recent commits, changed files, and diffs.",
        tags=("git", "history", "changes"),
        steps=(
            WorkflowStepTemplate(
                "Repository State",
                "Inspect git status, active files, and current branch for {task}.",
                "git",
                "Current git state is known.",
            ),
            WorkflowStepTemplate(
                "Recent History",
                "Review recent repository changes and commits for {task}.",
                "git",
                "Recent commit evidence is collected.",
            ),
            WorkflowStepTemplate(
                "Change Summary",
                "Summarize repository change evidence for {task}.",
                "analysis",
                "Change impact is explained.",
            ),
        ),
    ),
    "repository_exploration": WorkflowTemplate(
        name="Repository Exploration Workflow",
        workflow_type="repository_exploration",
        description="Locate files, modules, symbols, and repository behavior.",
        tags=("repository", "lookup", "exploration"),
        steps=(
            WorkflowStepTemplate(
                "Memory Lookup",
                "Check repository memory for {task}.",
                "memory",
                "Known repository facts are reused.",
            ),
            WorkflowStepTemplate(
                "Repository Search",
                "Search files, symbols, and dependencies for {task}.",
                "repository",
                "Relevant repository evidence is found.",
            ),
            WorkflowStepTemplate(
                "Explanation",
                "Explain where the behavior lives for {task}.",
                "analysis",
                "Repository map is summarized.",
            ),
        ),
    ),
    "architecture_analysis": WorkflowTemplate(
        name="Architecture Analysis Workflow",
        workflow_type="architecture_analysis",
        description="Review high-level modules, relationships, entry points, and system design.",
        tags=("architecture", "modules", "design"),
        steps=(
            WorkflowStepTemplate(
                "Memory Architecture Review",
                "Review repository memory architecture facts for {task}.",
                "memory",
                "Known architecture facts are reused.",
            ),
            WorkflowStepTemplate(
                "Module Survey",
                "Analyze repository modules, entry points, and relationships for {task}.",
                "repository",
                "Architecture areas are identified.",
            ),
            WorkflowStepTemplate(
                "Relationship Summary",
                "Summarize module relationships and architecture for {task}.",
                "analysis",
                "Architecture summary is prepared.",
            ),
        ),
    ),
    "test_failure_investigation": WorkflowTemplate(
        name="Test Failure Investigation Workflow",
        workflow_type="test_failure_investigation",
        description="Investigate failing tests with validation output and relevant code evidence.",
        tags=("tests", "validation", "failure"),
        steps=(
            WorkflowStepTemplate(
                "Run Validation",
                "Check why tests are failing for {task}.",
                "validation",
                "Test failure evidence is collected.",
            ),
            WorkflowStepTemplate(
                "Locate Failing Areas",
                "Search repository files and symbols related to failing tests for {task}.",
                "repository",
                "Likely failing code areas are identified.",
            ),
            WorkflowStepTemplate(
                "Failure Summary",
                "Summarize test failures and recommended next checks for {task}.",
                "analysis",
                "Test failure findings are prepared.",
            ),
        ),
    ),
    "dependency_investigation": WorkflowTemplate(
        name="Dependency Investigation Workflow",
        workflow_type="dependency_investigation",
        description="Analyze imports, dependency edges, dependents, and coupling.",
        tags=("dependencies", "imports", "coupling"),
        steps=(
            WorkflowStepTemplate(
                "Dependency Search",
                "Find dependency links and dependents for {task}.",
                "repository",
                "Dependency graph evidence is collected.",
            ),
            WorkflowStepTemplate(
                "Related Files",
                "Inspect related files and modules for {task}.",
                "repository",
                "Related modules are understood.",
            ),
            WorkflowStepTemplate(
                "Dependency Summary",
                "Summarize dependency relationships for {task}.",
                "analysis",
                "Dependency findings are prepared.",
            ),
        ),
    ),
    "performance_investigation": WorkflowTemplate(
        name="Performance Investigation Workflow",
        workflow_type="performance_investigation",
        description="Analyze slow paths, expensive operations, loops, IO, caching, and tests.",
        tags=("performance", "latency", "optimization"),
        steps=(
            WorkflowStepTemplate(
                "Locate Performance Path",
                "Locate performance-sensitive files and code paths for {task}.",
                "repository",
                "Performance areas are found.",
            ),
            WorkflowStepTemplate(
                "Evidence Collection",
                "Inspect caching, IO, loops, validation, and recent changes for {task}.",
                "analysis",
                "Performance evidence is collected.",
            ),
            WorkflowStepTemplate(
                "Performance Summary",
                "Summarize likely performance bottlenecks for {task}.",
                "analysis",
                "Performance findings are prepared.",
            ),
        ),
    ),
}


def predefined_templates() -> dict[WorkflowType, WorkflowTemplate]:
    """Return all predefined workflow templates."""
    return dict(PREDEFINED_WORKFLOWS)
