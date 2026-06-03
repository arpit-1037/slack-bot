"""Centralized planning prompt text for future LLM-assisted planning."""

from __future__ import annotations

from src.planning.execution_models import TaskTypeName

PLANNING_SYSTEM_PROMPT = """You are a planning engine. Produce structured plans only.
Do not execute commands, modify files, commit changes, push branches, or deploy systems.
Focus on task decomposition, dependencies, risk, expected outcomes, and verification."""

BUG_INVESTIGATION_PROMPT = """Analyze the failing behavior, locate the relevant flow,
identify reproduction and state signals, design the smallest fix, and plan regression coverage."""

REFACTOR_PROMPT = """Map the current structure, identify coupling, choose a minimal refactor path,
preserve behavior, and plan targeted verification."""

FEATURE_IMPLEMENTATION_PROMPT = """Locate existing integration points, design the target behavior,
plan bounded implementation steps, include configuration or persistence needs, and verify integration."""

DEBUGGING_PROMPT = """Use stacktrace, repository, dependency, and git signals to plan a focused debugging path.
Do not call execution tools or apply fixes."""

REPOSITORY_ANALYSIS_PROMPT = """Retrieve relevant files, symbols, dependencies, and recent git context,
then explain how those signals shape the plan."""

DOCUMENTATION_PROMPT = """Identify the audience and source of truth, plan concise documentation updates,
include examples, and verify consistency with existing behavior."""

TESTING_PROMPT = """Identify behavior under test, choose unit or integration coverage,
plan fixtures and assertions, and include verification commands only as reviewable recommendations."""

CONFIGURATION_PROMPT = """Locate configuration readers and defaults, plan environment updates,
include validation and rollback notes, and avoid exposing secrets."""

GIT_TASK_PROMPT = """Inspect read-only git context, plan the intended repository action,
identify safeguards, and produce reviewable command recommendations without running them."""

PROMPTS_BY_TASK_TYPE: dict[TaskTypeName, str] = {
    "Bug Fix": BUG_INVESTIGATION_PROMPT,
    "Refactor": REFACTOR_PROMPT,
    "Feature Development": FEATURE_IMPLEMENTATION_PROMPT,
    "Investigation": DEBUGGING_PROMPT,
    "Documentation": DOCUMENTATION_PROMPT,
    "Git Task": GIT_TASK_PROMPT,
    "Testing": TESTING_PROMPT,
    "Configuration Change": CONFIGURATION_PROMPT,
    "Repository Exploration": REPOSITORY_ANALYSIS_PROMPT,
}


def get_planning_prompt(task_type: TaskTypeName) -> str:
    """Return the centralized prompt text for a task type."""
    return PROMPTS_BY_TASK_TYPE.get(task_type, REPOSITORY_ANALYSIS_PROMPT)


def prompt_catalog() -> dict[str, str]:
    """Return all planning prompt text keyed by task type."""
    return {task_type: prompt for task_type, prompt in PROMPTS_BY_TASK_TYPE.items()}
