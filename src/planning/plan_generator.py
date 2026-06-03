"""Generate deterministic structured plans from task analysis and context."""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.planning.execution_models import (
    Plan,
    PlanStep,
    PlanningContext,
    RiskLevelName,
    TaskAnalysis,
    TaskTypeName,
)
from src.planning.planning_prompts import get_planning_prompt


@dataclass(frozen=True)
class StepTemplate:
    """Reusable template for deterministic plan steps."""

    title: str
    description: str
    expected_outcome: str


class PlanGenerator:
    """Convert analyzed tasks into ordered plans without executing them."""

    _templates: dict[TaskTypeName, tuple[StepTemplate, ...]] = {
        "Bug Fix": (
            StepTemplate(
                "Locate Failing Flow",
                "Identify the entry point, affected handlers, and current behavior for the reported bug.",
                "The likely failing path is known before any fix is designed.",
            ),
            StepTemplate(
                "Review Reproduction Signals",
                "Define the inputs, state, logs, or Slack event shape needed to reproduce the issue.",
                "The failure can be reproduced or reasoned about from concrete signals.",
            ),
            StepTemplate(
                "Inspect Dependencies And State",
                "Check nearby dependencies, persistence, caches, retries, and recent repository activity.",
                "The plan accounts for upstream and downstream behavior.",
            ),
            StepTemplate(
                "Design Minimal Fix",
                "Choose the smallest behavior change that addresses the root cause while preserving existing flow.",
                "The intended fix is narrow and reviewable.",
            ),
            StepTemplate(
                "Plan Targeted Code Update",
                "Identify exactly which functions or modules would need a code change.",
                "Implementation scope is bounded to relevant files and symbols.",
            ),
            StepTemplate(
                "Plan Regression Coverage",
                "Identify unit or integration tests that would fail before the fix and pass afterward.",
                "The bug has a concrete regression test strategy.",
            ),
            StepTemplate(
                "Plan Verification",
                "Define syntax, tests, and behavior checks needed to validate the fix after implementation.",
                "The fix can be verified without relying on guesswork.",
            ),
        ),
        "Refactor": (
            StepTemplate(
                "Map Current Structure",
                "Identify the files, symbols, and dependency edges involved in the current implementation.",
                "The existing design and coupling points are visible.",
            ),
            StepTemplate(
                "Define Refactor Boundary",
                "Choose the smallest module or function boundary that improves structure without broad churn.",
                "The refactor has a clear scope.",
            ),
            StepTemplate(
                "Preserve Public Behavior",
                "List inputs, outputs, side effects, and compatibility constraints that must remain unchanged.",
                "Behavioral invariants are explicit.",
            ),
            StepTemplate(
                "Plan Internal Restructure",
                "Order the extraction, rename, or dependency cleanup steps behind the chosen boundary.",
                "The restructure can be reviewed step by step.",
            ),
            StepTemplate(
                "Plan Test Adjustments",
                "Identify existing tests to keep and any focused tests needed for the new shape.",
                "Coverage follows the refactor without masking behavior changes.",
            ),
            StepTemplate(
                "Plan Verification",
                "Define formatting, syntax, and test checks needed after the refactor.",
                "The refactor can be validated safely.",
            ),
        ),
        "Feature Development": (
            StepTemplate(
                "Identify Existing Flow",
                "Locate the current entry points, services, models, and configuration related to the feature.",
                "The feature is anchored to existing architecture.",
            ),
            StepTemplate(
                "Locate Integration Points",
                "Find the functions, classes, routes, or handlers where the feature would connect.",
                "The plan knows where the feature belongs.",
            ),
            StepTemplate(
                "Design Target Behavior",
                "Specify expected inputs, outputs, edge cases, and compatibility constraints.",
                "The feature behavior is clear before implementation.",
            ),
            StepTemplate(
                "Plan Implementation Changes",
                "Break the implementation into ordered, reviewable module-level changes.",
                "The code change sequence is bounded and dependency-aware.",
            ),
            StepTemplate(
                "Plan Configuration Or Persistence",
                "Identify whether environment variables, schemas, storage, or defaults need updates.",
                "Supporting configuration and data changes are explicit.",
            ),
            StepTemplate(
                "Plan Tests And Examples",
                "Identify unit tests, integration tests, and examples that prove the new behavior.",
                "The feature has a test and example strategy.",
            ),
            StepTemplate(
                "Plan Integration Verification",
                "Define end-to-end checks for the feature inside the existing workflow.",
                "The implementation can be verified in context.",
            ),
        ),
        "Investigation": (
            StepTemplate(
                "Clarify Question",
                "Restate the unknown and identify the repository areas most likely to answer it.",
                "The investigation has a precise target.",
            ),
            StepTemplate(
                "Collect Repository Evidence",
                "Review relevant files, symbols, dependencies, and state signals.",
                "The answer is grounded in repository evidence.",
            ),
            StepTemplate(
                "Compare Possible Causes",
                "Separate confirmed facts from hypotheses and rank likely explanations.",
                "The investigation avoids premature conclusions.",
            ),
            StepTemplate(
                "Plan Follow-Up Checks",
                "List read-only checks or tests that would confirm the leading explanation.",
                "The next validation steps are explicit.",
            ),
            StepTemplate(
                "Summarize Findings",
                "Prepare a concise explanation with evidence and residual uncertainty.",
                "The user gets a clear answer and next steps.",
            ),
        ),
        "Documentation": (
            StepTemplate(
                "Identify Source Of Truth",
                "Locate the code, configuration, or behavior the documentation must describe.",
                "Documentation is based on current implementation.",
            ),
            StepTemplate(
                "Define Audience And Scope",
                "Decide whether the docs are for setup, usage, architecture, operations, or tests.",
                "The documentation target is clear.",
            ),
            StepTemplate(
                "Plan Documentation Changes",
                "Outline the sections, examples, and terminology to update.",
                "The documentation update is structured.",
            ),
            StepTemplate(
                "Plan Consistency Checks",
                "Identify commands, examples, or file paths that must be checked against the repo.",
                "The docs can be verified against real behavior.",
            ),
        ),
        "Git Task": (
            StepTemplate(
                "Inspect Repository State",
                "Use read-only branch, status, diff, and recent history context to understand the request.",
                "The git task is grounded in current repository state.",
            ),
            StepTemplate(
                "Define Intended Git Outcome",
                "Clarify whether the goal is review, staging, committing, branching, or history analysis.",
                "The desired git result is explicit.",
            ),
            StepTemplate(
                "Identify Safeguards",
                "Check for uncommitted changes, branch risk, missing messages, or destructive operations.",
                "Risky git behavior is called out before any action.",
            ),
            StepTemplate(
                "Prepare Reviewable Command Plan",
                "List the commands that could be run later, with preconditions and expected output.",
                "The git sequence is reviewable and not executed.",
            ),
            StepTemplate(
                "Plan Verification",
                "Define read-only status or log checks to confirm the outcome after user-approved execution.",
                "The git result can be verified.",
            ),
        ),
        "Testing": (
            StepTemplate(
                "Identify Behavior Under Test",
                "Locate the feature, bug, or module that needs coverage.",
                "The tests target meaningful behavior.",
            ),
            StepTemplate(
                "Choose Test Level",
                "Decide between unit, integration, regression, or configuration coverage.",
                "The plan uses the right test scope.",
            ),
            StepTemplate(
                "Plan Fixtures And Assertions",
                "Define inputs, mocks, repository state, and expected assertions.",
                "The tests are deterministic.",
            ),
            StepTemplate(
                "Plan Test Implementation",
                "Identify test files and helper code that would need updates.",
                "The test changes are scoped.",
            ),
            StepTemplate(
                "Plan Verification",
                "Define the specific test command and expected pass criteria for later execution.",
                "The coverage can be verified.",
            ),
        ),
        "Configuration Change": (
            StepTemplate(
                "Locate Configuration Readers",
                "Find environment variables, defaults, provider setup, and configuration consumers.",
                "The configuration path is known.",
            ),
            StepTemplate(
                "Define Desired Configuration",
                "Specify the new default, override behavior, and secret-handling constraints.",
                "The intended configuration behavior is clear.",
            ),
            StepTemplate(
                "Plan Minimal Config Update",
                "Identify code, example env, or docs updates needed for the configuration change.",
                "The configuration change is bounded.",
            ),
            StepTemplate(
                "Plan Validation",
                "Define tests and smoke checks that prove the configuration is read correctly.",
                "Configuration behavior can be verified.",
            ),
            StepTemplate(
                "Plan Rollback Notes",
                "Document how to return to the previous configuration if needed.",
                "The change has a safe fallback path.",
            ),
        ),
        "Repository Exploration": (
            StepTemplate(
                "Parse Lookup Intent",
                "Identify the files, symbols, behavior, or dependencies the user wants to find.",
                "The lookup has a clear target.",
            ),
            StepTemplate(
                "Retrieve Ranked Context",
                "Use repository and hybrid retrieval signals to rank relevant files and symbols.",
                "The most relevant repository areas are selected.",
            ),
            StepTemplate(
                "Inspect Dependency Links",
                "Review dependencies, dependents, and recent git activity around the selected files.",
                "The lookup includes nearby context.",
            ),
            StepTemplate(
                "Prepare Explanation",
                "Summarize where the behavior lives and why the selected files matter.",
                "The user gets a concise repository map.",
            ),
        ),
    }

    _planning_prefix_patterns = (
        r"^(?:create|make|generate|draft|prepare|give me)\s+(?:an?\s+)?(?:implementation\s+|execution\s+|refactor\s+|debugging\s+)?plan\s+(?:for|to|about|of)?\s*",
        r"^how would you\s+",
        r"^how should (?:i|we|you)\s+",
        r"^what is the plan\s+(?:for|to)?\s*",
    )

    def generate_plan(
        self,
        task: str,
        analysis: TaskAnalysis,
        context: PlanningContext | None = None,
    ) -> Plan:
        """Generate a structured, dependency-aware plan for the task."""
        context = context or PlanningContext(task=task)
        goal = self._goal_from_task(task)
        templates = self._templates[analysis.task_type]
        steps = [
            self._build_step(index=index, template=template, analysis=analysis, context=context)
            for index, template in enumerate(templates, start=1)
        ]
        return Plan(
            goal=goal,
            analysis=analysis,
            context=context,
            steps=steps,
            explanation=self._explain_generation(analysis, context),
            metadata={
                "planning_prompt": get_planning_prompt(analysis.task_type),
                "deterministic": True,
                "executes_plan": False,
            },
        )

    def _build_step(
        self,
        index: int,
        template: StepTemplate,
        analysis: TaskAnalysis,
        context: PlanningContext,
    ) -> PlanStep:
        """Create one numbered plan step from a template."""
        step_id = f"step-{index}"
        dependencies = [] if index == 1 else [f"step-{index - 1}"]
        description = self._contextualize_description(index, template.description, context)
        return PlanStep(
            id=step_id,
            title=template.title,
            description=description,
            dependencies=dependencies,
            risk_level=self._risk_for_step(template, analysis),
            expected_outcome=template.expected_outcome,
        )

    def _contextualize_description(self, index: int, description: str, context: PlanningContext) -> str:
        """Add repository and git hints to early discovery steps."""
        hints = []
        if index <= 2 and context.repository_files:
            files = ", ".join(file.path for file in context.repository_files[:3])
            hints.append(f"Prioritize retrieved files: {files}.")
        if index <= 3 and context.repository_symbols:
            symbols = ", ".join(symbol.name for symbol in context.repository_symbols[:4])
            hints.append(f"Inspect retrieved symbols: {symbols}.")
        if index <= 3 and context.git.has_context:
            active = context.git.changed_files + context.git.staged_files + context.git.untracked_files
            if active:
                hints.append(f"Account for active git files: {', '.join(active[:4])}.")
            elif context.git.recent_changed_files:
                hints.append(f"Check recent git files: {', '.join(context.git.recent_changed_files[:4])}.")
        if index <= 2 and context.debug_notes:
            hints.append("Use debugging notes: " + " ".join(context.debug_notes[:2]))
        return " ".join([description] + hints)

    def _risk_for_step(self, template: StepTemplate, analysis: TaskAnalysis) -> RiskLevelName:
        """Estimate plan-step risk from task complexity and step purpose."""
        text = f"{template.title} {template.description}".lower()
        if any(signal in text for signal in ("implementation", "code update", "config update", "git")):
            if analysis.complexity in {"Large", "Very Large"}:
                return "High"
            return "Medium"
        if any(signal in text for signal in ("rollback", "safeguard", "persistence", "migration")):
            return "Medium"
        return "Low"

    def _goal_from_task(self, task: str) -> str:
        """Derive a concise goal from the user task."""
        goal = " ".join(task.split()).strip()
        for pattern in self._planning_prefix_patterns:
            goal = re.sub(pattern, "", goal, flags=re.IGNORECASE).strip()
        goal = goal.strip(" .:-")
        if not goal:
            return "Create an implementation plan"
        return goal[:1].upper() + goal[1:]

    def _explain_generation(self, analysis: TaskAnalysis, context: PlanningContext) -> list[str]:
        """Explain why the generated steps are ordered as they are."""
        explanation = [
            analysis.reasoning,
            "Discovery steps come before design, design comes before planned changes, and verification comes last.",
            "The plan is thinking-only and does not execute code, git commands, or filesystem writes.",
        ]
        if context.repository_files:
            explanation.append(
                "Repository retrieval selected: "
                + ", ".join(file.path for file in context.repository_files[:5])
            )
        if context.git.has_context:
            explanation.append(
                f"Read-only git context attached for branch {context.git.branch or 'unknown'}."
            )
        if context.retrieval_explanations:
            explanation.append(
                "Hybrid retrieval signals used: "
                + " | ".join(context.retrieval_explanations[:3])
            )
        return explanation


_default_generator = PlanGenerator()


def generate_plan(
    task: str,
    analysis: TaskAnalysis,
    context: PlanningContext | None = None,
) -> Plan:
    """Generate a deterministic plan using the default generator."""
    return _default_generator.generate_plan(task, analysis, context=context)
