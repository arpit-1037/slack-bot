"""Execute planned task actions and call LLM providers when needed."""

from __future__ import annotations

from src.debugging.repository_debugger import RepositoryDebugger
from src.llm.provider_router import ProviderRouter
from src.modification.patch_generator import PatchGenerator
from src.modification.repository_modifier import RepositoryModifier
from src.planner.task_planner import TaskPlan
from src.prompts.prompt_builder import PromptBuilder
from src.tools.git_tool import GitTool
from src.tools.repository_tool import RepositoryTool
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
            "request_id=%s executing plan intent=%s git_action=%s raw_git=%s git_context=%s repo_context=%s web=%s repo_debug=%s repo_modify=%s",
            request_id,
            plan.intent,
            plan.run_git_action,
            plan.return_raw_git_diff,
            plan.needs_git_context,
            plan.needs_repository_context,
            plan.needs_web_search,
            plan.use_repository_debugger,
            plan.use_repository_modifier,
        )

        if plan.direct_response is not None:
            return plan.direct_response

        if plan.run_git_action:
            return self.git_tool.run_git_action(plan.clean_task)

        if plan.return_raw_git_diff:
            return self.git_tool.get_raw_diff()

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
            return self.repository_modifier.modify(
                project_path=self.git_tool.repo_path,
                task=plan.clean_task,
                thread_ts=thread_ts,
                channel=channel,
                slack_user=slack_user,
                request_id=request_id,
            )

        git_context = "Not needed for this task."
        code_context = "Not needed for this task."
        search_context = "Not needed for this task."

        if plan.needs_git_context:
            log.info("request_id=%s collecting git context", request_id)
            git_context = self.git_tool.get_git_context()

        if plan.needs_repository_context:
            log.info("request_id=%s scanning repository context", request_id)
            code_context = self.repository_tool.read_codebase(self.git_tool.repo_path)

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
