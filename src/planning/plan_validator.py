"""Validation for generated planning-engine plans."""

from __future__ import annotations

import re
from collections import Counter

from src.planning.execution_models import Plan, PlanStep, PlanValidationResult


class PlanValidator:
    """Validate plan structure, dependencies, ordering, and unsafe action wording."""

    _unsafe_patterns = (
        r"\bexecute\s+(?:the\s+)?(?:plan|command|commands)\b",
        r"\brun\s+(?:the\s+)?(?:command|commands|migration|deploy|deployment)\b",
        r"\bapply\s+(?:the\s+)?(?:patch|changes|migration)\b",
        r"\bcommit\s+(?:the\s+)?changes\b",
        r"\bpush\s+(?:the\s+)?(?:branch|changes|commits)\b",
        r"\bdeploy\b",
        r"\bgit\s+reset\b",
        r"\brm\s+-rf\b",
    )

    def validate_plan(self, plan: Plan) -> PlanValidationResult:
        """Return validation warnings and errors for a plan."""
        errors: list[str] = []
        warnings: list[str] = []

        if not plan.steps:
            errors.append("Plan has no steps.")
            return PlanValidationResult(valid=False, warnings=warnings, errors=errors)

        self._validate_duplicates(plan.steps, errors, warnings)
        self._validate_dependencies(plan.steps, errors)
        self._validate_circular_dependencies(plan.steps, errors)
        self._validate_unsafe_actions(plan.steps, errors, warnings)
        self._validate_expected_coverage(plan, warnings)

        return PlanValidationResult(valid=not errors, warnings=warnings, errors=errors)

    def _validate_duplicates(self, steps: list[PlanStep], errors: list[str], warnings: list[str]) -> None:
        """Detect duplicate ids and near-duplicate titles."""
        id_counts = Counter(step.id for step in steps)
        for step_id, count in id_counts.items():
            if count > 1:
                errors.append(f"Duplicate step id: {step_id}.")

        title_counts = Counter(self._normalize_title(step.title) for step in steps)
        for title, count in title_counts.items():
            if count > 1:
                warnings.append(f"Duplicate step title: {title}.")

    def _validate_dependencies(self, steps: list[PlanStep], errors: list[str]) -> None:
        """Validate dependency references and ordering."""
        step_ids = [step.id for step in steps]
        known_ids = set(step_ids)
        positions = {step_id: index for index, step_id in enumerate(step_ids)}

        for step in steps:
            for dependency in step.dependencies:
                if dependency not in known_ids:
                    errors.append(f"{step.id} depends on unknown step {dependency}.")
                    continue
                if positions[dependency] >= positions[step.id]:
                    errors.append(f"{step.id} depends on {dependency}, which is not earlier in the plan.")

    def _validate_circular_dependencies(self, steps: list[PlanStep], errors: list[str]) -> None:
        """Detect dependency cycles even when ordering checks do not catch them."""
        graph = {step.id: list(step.dependencies) for step in steps}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> bool:
            if step_id in visiting:
                return True
            if step_id in visited:
                return False
            visiting.add(step_id)
            for dependency in graph.get(step_id, []):
                if dependency in graph and visit(dependency):
                    return True
            visiting.remove(step_id)
            visited.add(step_id)
            return False

        for step in steps:
            if visit(step.id):
                errors.append(f"Circular dependency detected around {step.id}.")
                return

    def _validate_unsafe_actions(
        self,
        steps: list[PlanStep],
        errors: list[str],
        warnings: list[str],
    ) -> None:
        """Detect wording that implies the planner will execute actions."""
        for step in steps:
            text = f"{step.title} {step.description} {step.expected_outcome}".lower()
            for pattern in self._unsafe_patterns:
                if re.search(pattern, text):
                    warnings.append(
                        f"{step.id} contains execution-like wording; keep it as a recommendation only."
                    )
                    break

            if "autonomously" in text and any(word in text for word in ("modify", "execute", "commit")):
                errors.append(f"{step.id} suggests autonomous action.")

    def _validate_expected_coverage(self, plan: Plan, warnings: list[str]) -> None:
        """Warn when important planning surfaces are absent."""
        titles = " ".join(step.title.lower() for step in plan.steps)
        if plan.analysis.requires_repository_context and not any(
            signal in titles for signal in ("locate", "repository", "flow", "context", "configuration")
        ):
            warnings.append("Plan does not include an explicit repository discovery step.")
        if plan.analysis.task_type in {"Bug Fix", "Feature Development", "Refactor", "Testing"} and not any(
            signal in titles for signal in ("test", "verification", "coverage", "validate")
        ):
            warnings.append("Plan does not include an explicit test or verification step.")

    def _normalize_title(self, title: str) -> str:
        """Normalize a step title for duplicate detection."""
        return re.sub(r"\s+", " ", title.lower()).strip()


_default_validator = PlanValidator()


def validate_plan(plan: Plan) -> PlanValidationResult:
    """Validate a plan using the default validator."""
    return _default_validator.validate_plan(plan)
