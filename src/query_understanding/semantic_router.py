"""Semantic-ish deterministic routing to known assistant tools."""

from __future__ import annotations

import re

from src.query_understanding.understanding_models import IntentResult


_SHOW_WORDS = {
    "all",
    "available",
    "display",
    "exist",
    "exists",
    "give",
    "list",
    "names",
    "print",
    "show",
    "tell",
    "what",
}


class SemanticRouter:
    """Map equivalent developer requests to existing read-only tools."""

    def route_query(self, query: str) -> IntentResult:
        """Return the best semantic tool route for a normalized query."""
        task = _normalize(query)
        tokens = set(task.split())
        explanation_followup = bool(re.match(r"^(why|how)\b", task))

        branch_signals = _branch_signals(task, tokens)
        if branch_signals:
            confidence = 0.94 if not explanation_followup else 0.55
            return IntentResult(
                intent="git",
                confidence=confidence,
                tool_name="git.branch" if confidence >= 0.65 else None,
                tool_input={"include_remote": _wants_remote_branches(task, tokens)},
                reason="semantic branch-list request",
                signals=branch_signals,
            )

        log_signals = _log_signals(task, tokens)
        if log_signals:
            limit = _extract_limit(task) or 10
            return IntentResult(
                intent="git",
                confidence=0.91,
                tool_name="git.log",
                tool_input={"limit": limit},
                reason="semantic git-history request",
                signals=log_signals,
            )

        status_signals = _status_signals(task, tokens)
        if status_signals:
            return IntentResult(
                intent="git",
                confidence=0.9,
                tool_name="git.status",
                reason="semantic git-status request",
                signals=status_signals,
            )

        diff_signals = _diff_signals(task, tokens)
        if diff_signals:
            return IntentResult(
                intent="git",
                confidence=0.82,
                tool_name="git.diff",
                tool_input={"max_chars": 20000},
                reason="semantic git-diff request",
                signals=diff_signals,
            )

        return IntentResult(intent="general", confidence=0.35, reason="no semantic tool match")


def route_query(query: str) -> IntentResult:
    """Convenience wrapper for semantic routing."""
    return SemanticRouter().route_query(query)


def _branch_signals(task: str, tokens: set[str]) -> list[str]:
    signals: list[str] = []
    if {"branch", "branches"} & tokens:
        signals.append("branch-term")
    if tokens & _SHOW_WORDS:
        signals.append("display-term")
    if re.search(r"\bbranch\s+(?:list|names)\b", task):
        signals.append("branch-list-phrase")
    if re.search(r"\bwhat\s+branches\s+(?:exist|are|available)\b", task):
        signals.append("branch-existence-phrase")
    return signals if "branch-term" in signals and len(signals) >= 2 else []


def _log_signals(task: str, tokens: set[str]) -> list[str]:
    signals: list[str] = []
    if tokens & {"commit", "commits", "history", "log"}:
        signals.append("commit-history-term")
    if tokens & {"recent", "latest", "last", "show", "list", "display"}:
        signals.append("recency-or-display-term")
    if "git history" in task or "commit list" in task:
        signals.append("history-phrase")
    return signals if "commit-history-term" in signals and len(signals) >= 2 else []


def _status_signals(task: str, tokens: set[str]) -> list[str]:
    signals: list[str] = []
    if tokens & {"status", "staged", "unstaged", "untracked"}:
        signals.append("status-term")
    if "working tree" in task:
        signals.append("working-tree-phrase")
    if tokens & {"show", "list", "what", "current"}:
        signals.append("display-term")
    return signals if signals and ("status-term" in signals or "working-tree-phrase" in signals) else []


def _diff_signals(task: str, tokens: set[str]) -> list[str]:
    signals: list[str] = []
    if tokens & {"diff", "changed", "changes"}:
        signals.append("diff-term")
    if "what changed" in task or "show changes" in task:
        signals.append("change-phrase")
    return signals


def _wants_remote_branches(task: str, tokens: set[str]) -> bool:
    return bool(tokens & {"all", "remote", "remotes"} or "-a" in task or "--all" in task)


def _extract_limit(task: str) -> int | None:
    match = re.search(r"\b(?:last|latest|recent)\s+(\d{1,2})\b", task)
    if not match:
        return None
    return max(1, min(int(match.group(1)), 50))


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").lower()).strip()
