"""Execute planned task actions and call LLM providers when needed."""

from __future__ import annotations

from src.debugging.repository_debugger import RepositoryDebugger
from src.execution.execution_engine import ExecutionEngine
from src.llm.provider_router import ProviderRouter
from src.memory.repository_memory import RepositoryMemory
from src.modification.code_modifier import CodeModifier
from src.modification.patch_generator import PatchGenerator
from src.modification.repository_modifier import RepositoryModifier
from src.planning.planner import PlanningEngine
from src.planner.task_planner import TaskPlan
from src.prompts.prompt_builder import PromptBuilder
from src.tools.base_tool import ToolResult
from src.tools.git_tool import GitTool
from src.tools.repository_tool import RepositoryTool
from src.tools.tool_executor import ToolExecutor
from src.tools.web_search_tool import WebSearchTool
from src.utils.helpers import get_logger

log = get_logger(__name__)


class TaskExecutor:
    """Execute a TaskPlan using isolated tools and provider routing."""

    def __init__(
        self,
        git_tool: GitTool | None = None,
        repository_tool: RepositoryTool | None = None,
        web_search_tool: WebSearchTool | None = None,
        prompt_builder: PromptBuilder | None = None,
        provider_router: ProviderRouter | None = None,
        repository_debugger: RepositoryDebugger | None = None,
        repository_modifier: RepositoryModifier | None = None,
        code_modifier: CodeModifier | None = None,
        planning_engine: PlanningEngine | None = None,
        execution_engine: ExecutionEngine | None = None,
        repository_memory: RepositoryMemory | None = None,
        tool_executor: ToolExecutor | None = None,
    ) -> None:
        self.git_tool = git_tool or GitTool()
        self.repository_tool = repository_tool or RepositoryTool()
        self.web_search_tool = web_search_tool or WebSearchTool()
        self.prompt_builder = prompt_builder or PromptBuilder()
        self.provider_router = provider_router or ProviderRouter()
        self.repository_debugger = repository_debugger or RepositoryDebugger(
            provider_router=self.provider_router
        )
        self.repository_modifier = repository_modifier or RepositoryModifier(
            patch_generator=PatchGenerator(provider_router=self.provider_router)
        )
        self.code_modifier = code_modifier or CodeModifier(
            patch_generator=PatchGenerator(provider_router=self.provider_router)
        )
        self.planning_engine = planning_engine or PlanningEngine(git_tool=self.git_tool)
        self.execution_engine = execution_engine or ExecutionEngine(
            planning_engine=self.planning_engine
        )
        self.repository_memory = repository_memory or RepositoryMemory(self.git_tool.repo_path)
        self.tool_executor = tool_executor or ToolExecutor()

    def execute(
        self,
        plan: TaskPlan,
        thread_ts: str | None = None,
        channel: str | None = None,
        slack_user: str | None = None,
        request_id: str | None = None,
    ) -> str:
        """Execute the planned actions and return the Slack response text."""
        log.info(
            "request_id=%s executing plan intent=%s git_action=%s raw_git=%s selected_tool=%s git_context=%s repo_context=%s web=%s planning=%s execution=%s repo_debug=%s repo_modify=%s",
            request_id,
            plan.intent,
            plan.run_git_action,
            plan.return_raw_git_diff,
            plan.selected_tool_name or "none",
            plan.needs_git_context,
            plan.needs_repository_context,
            plan.needs_web_search,
            plan.use_planning_engine,
            plan.use_execution_engine,
            plan.use_repository_debugger,
            plan.use_repository_modifier,
        )

        if plan.direct_response is not None:
            return plan.direct_response

        if plan.run_git_action:
            return self.git_tool.run_git_action(plan.clean_task)

        if plan.return_raw_git_diff:
            return self.git_tool.get_raw_diff()

        if plan.selected_tool_name:
            tool_input = dict(plan.selected_tool_input)
            tool_input.setdefault("repo_path", self.git_tool.repo_path)
            log.info(
                "request_id=%s executing selected tool=%s input_keys=%s",
                request_id,
                plan.selected_tool_name,
                ",".join(sorted(tool_input.keys())),
            )
            result = self.tool_executor.execute_tool(plan.selected_tool_name, tool_input)
            return self._format_tool_result(result)

        if plan.use_planning_engine:
            planning_plan = self.planning_engine.create_plan(
                plan.clean_task,
                project_path=self.git_tool.repo_path,
                request_id=request_id,
            )
            return planning_plan.format_markdown()

        if plan.use_execution_engine:
            planning_plan = self.planning_engine.create_plan(
                plan.clean_task,
                project_path=self.git_tool.repo_path,
                request_id=request_id,
            )
            summary = self.execution_engine.execute_plan(
                planning_plan,
                project_path=self.git_tool.repo_path,
                request_id=request_id,
            )
            return summary.format_markdown()

        if plan.use_repository_debugger:
            return self.repository_debugger.debug(
                project_path=self.git_tool.repo_path,
                task=plan.clean_task,
                thread_ts=thread_ts,
                channel=channel,
                slack_user=slack_user,
                request_id=request_id,
            )

        if plan.use_repository_modifier:
            return self.code_modifier.modify_code(
                project_path=self.git_tool.repo_path,
                user_request=plan.clean_task,
                thread_ts=thread_ts,
                channel=channel,
                slack_user=slack_user,
                request_id=request_id,
            ).format_response()

        git_context = "Not needed for this task."
        code_context = "Not needed for this task."
        search_context = "Not needed for this task."

        if plan.needs_git_context:
            log.info("request_id=%s collecting git context", request_id)
            git_context = self.git_tool.get_git_context()

        if plan.needs_repository_context:
            memory_result = self._retrieve_repository_memory(plan.clean_task, request_id=request_id)
            if memory_result:
                return memory_result

            log.info("request_id=%s retrieving repository context", request_id)
            selection = self.repository_tool.select_context(
                project_path=self.git_tool.repo_path,
                task=plan.clean_task,
                request_id=request_id,
            )
            code_context = selection.context
            log.info(
                "request_id=%s retrieved repository context files=%d chars=%d",
                request_id,
                len(selection.selected_files),
                len(code_context),
            )

        if plan.needs_web_search:
            log.info("request_id=%s collecting web search context", request_id)
            search_context = self.web_search_tool.search(plan.clean_task)

        messages = self.prompt_builder.build_messages(
            plan.clean_task,
            thread_ts,
            channel,
            slack_user,
            plan.intent,
            git_context,
            code_context,
            search_context,
            request_id=request_id,
        )
        return self.provider_router.complete(messages, request_id=request_id)

    def _retrieve_repository_memory(self, task: str, request_id: str | None = None) -> str:
        """Return a formatted repository-memory hit, or an empty string on miss."""
        try:
            result = self.repository_memory.retrieve_memory(task, min_confidence=0.9)
        except Exception as error:
            log.warning("request_id=%s repository memory lookup skipped: %s", request_id, error)
            return ""
        if not result.hit:
            log.info(
                "request_id=%s repository memory miss confidence=%.4f",
                request_id,
                result.best_confidence,
            )
            return ""
        log.info(
            "request_id=%s repository memory hit confidence=%.4f",
            request_id,
            result.best_confidence,
        )
        return self.repository_memory.format_memory_result(result)

    def _format_tool_result(self, result: ToolResult) -> str:
        """Format structured tool output for a direct Slack response."""
        if not result.success:
            return f"*FAILED:* `{result.tool_name}`\n{result.error}"

        if result.tool_name == "git.branch":
            return self._format_git_branch_result(result)
        if result.tool_name == "git.log":
            return self._format_git_log_result(result)
        if result.tool_name == "git.status":
            return self._format_git_status_result(result)
        if result.tool_name == "git.diff":
            return self._format_git_diff_result(result)

        lines = [f"*Tool:* `{result.tool_name}`", "", "```"]
        for key, value in result.data.items():
            lines.append(f"{key}: {value}")
        lines.append("```")
        return "\n".join(lines)

    def _format_git_branch_result(self, result: ToolResult) -> str:
        data = result.data
        current = str(data.get("current_branch") or "Unknown")
        local = [str(branch) for branch in data.get("local_branches", [])]
        remote = [str(branch) for branch in data.get("remote_branches", [])]

        def mark_current(branch: str) -> str:
            return f"* {branch}" if branch == current else f"  {branch}"

        lines = [
            "*Current Branch:*",
            "```",
            current,
            "```",
            "",
            "*Local Branches:*",
            "```",
            *(mark_current(branch) for branch in local),
            "```",
        ]
        if remote:
            lines.extend(["", "*Remote Branches:*", "```", *remote, "```"])
        return "\n".join(lines)

    def _format_git_log_result(self, result: ToolResult) -> str:
        commits = result.data.get("commits", [])
        if not commits:
            return "*Recent Commits:*\n```\nNo commits found.\n```"
        lines = ["*Recent Commits:*", "```"]
        for commit in commits:
            lines.append(
                f"{commit.get('short_hash')} {commit.get('summary')} "
                f"({commit.get('author_name')}, {commit.get('authored_at')})"
            )
        lines.append("```")
        return "\n".join(lines)

    def _format_git_status_result(self, result: ToolResult) -> str:
        data = result.data
        lines = [
            "*Git Status:*",
            "```",
            f"branch: {data.get('branch') or 'Unknown'}",
            f"clean: {data.get('clean')}",
            "",
            "staged:",
            *self._format_items(data.get("staged_files", [])),
            "",
            "unstaged:",
            *self._format_items(data.get("unstaged_files", [])),
            "",
            "untracked:",
            *self._format_items(data.get("untracked_files", [])),
            "```",
        ]
        return "\n".join(lines)

    def _format_git_diff_result(self, result: ToolResult) -> str:
        data = result.data
        changed_files = data.get("changed_files", [])
        lines = [
            "*Changed Files:*",
            "```",
            *self._format_items(changed_files),
            "```",
            "",
            "*Diff Stat:*",
            "```",
            str(data.get("stat") or "No diff stat."),
            "```",
        ]
        diff = str(data.get("diff") or "")
        if diff:
            lines.extend(["", "*Diff:*", "```diff", diff, "```"])
        return "\n".join(lines)

    def _format_items(self, items: object) -> list[str]:
        if not isinstance(items, list) or not items:
            return ["None"]
        return [str(item) for item in items]
