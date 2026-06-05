"""Aggregate read-only tool outputs into findings reports."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from src.execution.execution_context import ExecutionContext
from src.execution.execution_models import ExecutionPlan, ExecutionResult, ExecutionSummary
from src.execution.execution_prompts import EXECUTION_TITLE, READ_ONLY_NOTICE, REPORT_SECTIONS


class ResultAggregator:
    """Merge tool outputs, remove duplication, and build investigation summaries."""

    def aggregate_results(
        self,
        plan: ExecutionPlan,
        results: list[ExecutionResult],
        context: ExecutionContext,
    ) -> ExecutionSummary:
        """Return a structured summary for executed read-only steps."""
        tools = self._tools(results)
        files = self._files(results)
        commits = self._commits(results)
        issues = self._issues(results)
        failures = self._failures(results)
        recommendations = self._recommendations(issues, failures, files)
        status = self._status(results, failures)
        summary = ExecutionSummary(
            execution_id=context.execution_id,
            plan_id=plan.id,
            goal=plan.goal,
            status=status,
            results=results,
            files_examined=files,
            commits_reviewed=commits,
            issues_found=issues,
            recommendations=recommendations,
            tools_executed=tools,
            failures=failures,
            metadata={
                "source_plan_id": plan.source_plan_id,
                "project_path": plan.project_path,
                "history_events": len(context.execution_history),
            },
        )
        return self._with_report(summary)

    def generate_findings_report(self, summary: ExecutionSummary) -> str:
        """Generate a Slack-friendly findings report from an execution summary."""
        lines = [
            EXECUTION_TITLE,
            READ_ONLY_NOTICE,
            "",
            f"*Goal:* {summary.goal}",
            f"*Status:* {summary.status}",
        ]
        lines.extend(self._section(REPORT_SECTIONS["tools"], summary.tools_executed))
        lines.extend(self._section(REPORT_SECTIONS["files"], summary.files_examined))
        lines.extend(self._section(REPORT_SECTIONS["commits"], summary.commits_reviewed))
        lines.extend(self._section(REPORT_SECTIONS["issues"], summary.issues_found or ["No blocking issues found from read-only evidence."]))
        lines.extend(self._section(REPORT_SECTIONS["recommendations"], summary.recommendations))
        if summary.failures:
            lines.extend(self._section(REPORT_SECTIONS["failures"], summary.failures))
        return "\n".join(lines)

    def _with_report(self, summary: ExecutionSummary) -> ExecutionSummary:
        return ExecutionSummary(
            execution_id=summary.execution_id,
            plan_id=summary.plan_id,
            goal=summary.goal,
            status=summary.status,
            results=summary.results,
            files_examined=summary.files_examined,
            commits_reviewed=summary.commits_reviewed,
            issues_found=summary.issues_found,
            recommendations=summary.recommendations,
            tools_executed=summary.tools_executed,
            failures=summary.failures,
            findings_report=self.generate_findings_report(summary),
            metadata=summary.metadata,
        )

    def _tools(self, results: list[ExecutionResult]) -> list[str]:
        return self._dedupe(
            str(tool_result.get("tool_name", ""))
            for result in results
            for tool_result in result.tool_results
            if tool_result.get("tool_name")
        )

    def _files(self, results: list[ExecutionResult]) -> list[str]:
        files: list[str] = []
        for tool_result in self._tool_results(results):
            data = tool_result.get("data", {})
            tool_name = tool_result.get("tool_name")
            if tool_name in {"system.file_reader", "system.file_metadata"}:
                self._append(files, data.get("path"))
            if tool_name == "repository.file_search":
                for match in data.get("matches", []):
                    self._append(files, match.get("path"))
            if tool_name == "repository.symbol_search":
                for match in data.get("matches", []):
                    self._append(files, match.get("path"))
            if tool_name == "repository.dependency_search":
                for match in data.get("matches", []):
                    self._append(files, match.get("path"))
                    for path in match.get("dependencies", [])[:3]:
                        self._append(files, path)
                    for path in match.get("dependents", [])[:3]:
                        self._append(files, path)
            if tool_name == "validation.syntax_check":
                checked = data.get("result", {}).get("checked_files", [])
                for path in checked:
                    self._append(files, path)
        return files[:20]

    def _commits(self, results: list[ExecutionResult]) -> list[str]:
        commits: list[str] = []
        for tool_result in self._tool_results(results):
            if tool_result.get("tool_name") != "git.log":
                continue
            for commit in tool_result.get("data", {}).get("commits", []):
                short_hash = str(commit.get("short_hash") or commit.get("hash") or "").strip()
                summary = str(commit.get("summary") or "").strip()
                if short_hash:
                    self._append(commits, f"{short_hash} {summary}".strip())
        return commits[:10]

    def _issues(self, results: list[ExecutionResult]) -> list[str]:
        issues: list[str] = []
        for result in results:
            for error in result.errors:
                self._append(issues, error)
            for tool_result in result.tool_results:
                if not tool_result.get("success"):
                    self._append(
                        issues,
                        f"{tool_result.get('tool_name')}: {tool_result.get('error') or 'tool failed'}",
                    )
                    continue
                data = tool_result.get("data", {})
                tool_name = tool_result.get("tool_name")
                if tool_name in {"validation.pytest", "validation.lint", "validation.syntax_check"}:
                    if data.get("passed") is False:
                        result_data = data.get("result", {})
                        summary = result_data.get("summary") if isinstance(result_data, dict) else ""
                        self._append(issues, summary or f"{tool_name} reported a failure.")
        return issues[:12]

    def _failures(self, results: list[ExecutionResult]) -> list[str]:
        failures: list[str] = []
        for result in results:
            if result.status == "failure":
                self._append(failures, f"{result.step_id}: {result.title}")
            for tool_result in result.tool_results:
                if not tool_result.get("success"):
                    self._append(
                        failures,
                        f"{tool_result.get('tool_name')}: {tool_result.get('error') or 'tool failed'}",
                    )
        return failures[:12]

    def _recommendations(
        self,
        issues: list[str],
        failures: list[str],
        files: list[str],
    ) -> list[str]:
        recommendations: list[str] = []
        if issues:
            recommendations.append("Use the failing validation or tool output above as the next investigation target.")
        if failures:
            recommendations.append("Re-run failed read-only checks after addressing their reported cause.")
        if files:
            recommendations.append("Prioritize the listed files for any follow-up fix or deeper review.")
        if not recommendations:
            recommendations.append("No immediate code change is implied by the read-only evidence.")
        return recommendations

    def _status(self, results: list[ExecutionResult], failures: list[str]) -> str:
        if not results:
            return "skipped"
        if failures:
            return "partial" if any(result.status == "success" for result in results) else "failure"
        if any(result.status == "partial" for result in results):
            return "partial"
        return "success"

    def _tool_results(self, results: list[ExecutionResult]) -> list[dict[str, Any]]:
        return [
            tool_result
            for result in results
            for tool_result in result.tool_results
        ]

    def _section(self, title: str, items: list[str]) -> list[str]:
        lines = ["", title]
        if not items:
            lines.append("- None")
            return lines
        lines.extend(f"- {item}" for item in items[:12])
        return lines

    def _append(self, items: list[str], value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in items:
            items.append(text)

    def _dedupe(self, items: Any) -> list[str]:
        ordered: OrderedDict[str, None] = OrderedDict()
        for item in items:
            text = str(item or "").strip()
            if text:
                ordered[text] = None
        return list(ordered)


_default_aggregator: ResultAggregator | None = None


def default_result_aggregator() -> ResultAggregator:
    """Return a lazily created result aggregator."""
    global _default_aggregator
    if _default_aggregator is None:
        _default_aggregator = ResultAggregator()
    return _default_aggregator


def aggregate_results(
    plan: ExecutionPlan,
    results: list[ExecutionResult],
    context: ExecutionContext,
) -> ExecutionSummary:
    """Aggregate read-only execution results using the default aggregator."""
    return default_result_aggregator().aggregate_results(plan, results, context)


def generate_findings_report(summary: ExecutionSummary) -> str:
    """Generate a Slack-friendly findings report using the default aggregator."""
    return default_result_aggregator().generate_findings_report(summary)
