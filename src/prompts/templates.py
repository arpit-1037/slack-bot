"""Prompt template helpers."""

from __future__ import annotations


def build_user_message(
    task: str,
    intent: str,
    git_context: str,
    code_context: str,
    search_context: str,
) -> str:
    """Build the final task message sent to LLM providers."""
    return f"""============================
DETECTED INTENT
============================
{intent}

============================
GIT HISTORY & REPO STATE
============================
{git_context}

============================
CURRENT PROJECT CODE
============================
{code_context}

============================
WEB SEARCH RESULTS
============================
{search_context}

============================
TASK
============================
{task}"""
