"""Main repository-aware debugging orchestrator."""

from __future__ import annotations

from src.debugging.bug_context_builder import BugContextBuilder
from src.debugging.debug_prompt_builder import DebugPromptBuilder
from src.debugging.stacktrace_parser import StacktraceParser
from src.llm.provider_router import ProviderRouter
from src.utils.helpers import get_logger

log = get_logger(__name__)


class RepositoryDebugger:
    """Coordinate stacktrace parsing, context building, prompt generation, and LLM analysis."""

    def __init__(
        self,
        stacktrace_parser: StacktraceParser | None = None,
        bug_context_builder: BugContextBuilder | None = None,
        debug_prompt_builder: DebugPromptBuilder | None = None,
        provider_router: ProviderRouter | None = None,
    ) -> None:
        self.stacktrace_parser = stacktrace_parser or StacktraceParser()
        self.bug_context_builder = bug_context_builder or BugContextBuilder()
        self.debug_prompt_builder = debug_prompt_builder or DebugPromptBuilder()
        self.provider_router = provider_router or ProviderRouter()

    def debug(
        self,
        project_path: str,
        task: str,
        thread_ts: str | None = None,
        channel: str | None = None,
        slack_user: str | None = None,
        request_id: str | None = None,
    ) -> str:
        """Run the repository-aware debugging flow and return provider analysis."""
        log.info("request_id=%s repository debugger started", request_id)
        stacktrace = self.stacktrace_parser.parse(task)
        bug_context = self.bug_context_builder.build(
            project_path=project_path,
            bug_description=task,
            stacktrace=stacktrace,
            request_id=request_id,
        )
        messages = self.debug_prompt_builder.build_messages(
            task=task,
            bug_context=bug_context,
            thread_ts=thread_ts,
            channel=channel,
            slack_user=slack_user,
            request_id=request_id,
        )
        log.info(
            "request_id=%s repository debugger prompt messages=%d selected_files=%d",
            request_id,
            len(messages),
            len(bug_context.files),
        )
        return self.provider_router.complete(messages, request_id=request_id)
