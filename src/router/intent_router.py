"""Intent classification for incoming Slack tasks."""

from __future__ import annotations

from src.tools.git_tool import is_git_action_query, is_git_query


class IntentRouter:
    """Classify task text into the small set of supported execution intents."""

    def classify(self, task: str) -> str:
        """Return the detected intent name for a cleaned task."""
        task_lower = task.lower().strip()

        greeting_words = {
            "hi", "hello", "hey", "good morning", "good afternoon",
            "good evening", "yo", "hola", "hii", "heyy",
        }
        if task_lower in greeting_words:
            return "greeting"

        if is_git_action_query(task_lower):
            return "git_action"

        if is_git_query(task_lower):
            return "git"

        generic_code_signals = [
            "write code", "code in", "example in", "program in",
            "reverse string", "java code", "python code", "javascript code",
            "c++ code", "php code", "laravel code", "sql query", "regex",
            "algorithm", "function to", "snippet", "syntax",
        ]
        if any(signal in task_lower for signal in generic_code_signals):
            return "generic_code"

        project_debug_signals = [
            "bug", "error", "issue", "not working", "failing", "broken",
            "fix this", "debug", "trace", "exception", "why is this failing",
            "check this code", "review this file",
        ]
        if any(signal in task_lower for signal in project_debug_signals):
            return "project_debug"

        web_signals = [
            "latest", "current", "today", "news", "install", "documentation",
            "docs", "version", "release",
        ]
        if any(signal in task_lower for signal in web_signals):
            return "web"

        return "general"


def classify_intent(task: str) -> str:
    """Compatibility helper for legacy callers."""
    return IntentRouter().classify(task)


def greeting_response() -> str:
    """Return the short greeting used by the existing bot."""
    return "Hey! Send me a task or question and I will help."
