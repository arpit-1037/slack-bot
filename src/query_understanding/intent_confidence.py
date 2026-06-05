"""Confidence scoring for normalized intent decisions."""

from __future__ import annotations

import re

from src.query_understanding.semantic_router import SemanticRouter
from src.query_understanding.understanding_models import IntentResult


def score_intent_confidence(
    query: str,
    classifier_intent: str | None = None,
    semantic_result: IntentResult | None = None,
) -> list[IntentResult]:
    """Return confidence scores for git, repository, web, and general intents."""
    task = re.sub(r"\s+", " ", (query or "").lower()).strip()
    tokens = set(re.findall(r"[a-z0-9_+-]+", task))
    semantic = semantic_result or SemanticRouter().route_query(task)

    git_signals = _matches(
        task,
        tokens,
        {
            "git",
            "branch",
            "branches",
            "commit",
            "commits",
            "diff",
            "history",
            "log",
            "status",
            "staged",
            "unstaged",
            "untracked",
        },
        ["working tree", "what changed", "show changes"],
    )
    repository_signals = _matches(
        task,
        tokens,
        {
            "repository",
            "project",
            "codebase",
            "file",
            "files",
            "module",
            "class",
            "function",
            "method",
            "service",
            "handler",
            "controller",
            "route",
            "slack",
            "auth",
            "authentication",
        },
        ["where is", "which file", "what file", "related to"],
    )
    web_signals = _matches(
        task,
        tokens,
        {"latest", "current", "today", "news", "docs", "documentation", "version"},
        [],
    )

    git_score = _score(0.28, git_signals, classifier_intent == "git")
    if semantic.intent == "git":
        git_score = max(git_score, semantic.confidence)
    repository_score = _score(0.26, repository_signals, classifier_intent == "project_retrieval")
    web_score = _score(0.18, web_signals, classifier_intent == "web")
    general_score = max(0.1, 0.62 - max(git_score, repository_score, web_score) / 2)
    if classifier_intent == "general":
        general_score = max(general_score, 0.45)

    results = [
        IntentResult(
            intent="git",
            confidence=round(min(git_score, 0.98), 2),
            tool_name=semantic.tool_name if semantic.intent == "git" else None,
            tool_input=semantic.tool_input if semantic.intent == "git" else {},
            reason=semantic.reason if semantic.intent == "git" else "keyword confidence",
            signals=git_signals + (semantic.signals if semantic.intent == "git" else []),
        ),
        IntentResult(
            intent="project_retrieval",
            confidence=round(min(repository_score, 0.95), 2),
            reason="repository confidence",
            signals=repository_signals,
        ),
        IntentResult(
            intent="web",
            confidence=round(min(web_score, 0.92), 2),
            reason="web confidence",
            signals=web_signals,
        ),
        IntentResult(
            intent="general",
            confidence=round(min(general_score, 0.9), 2),
            reason="fallback confidence",
        ),
    ]
    return sorted(results, key=lambda result: result.confidence, reverse=True)


def _matches(
    task: str,
    tokens: set[str],
    keywords: set[str],
    phrases: list[str],
) -> list[str]:
    signals = sorted(tokens & keywords)
    signals.extend(phrase for phrase in phrases if phrase in task)
    return signals


def _score(base: float, signals: list[str], classifier_match: bool) -> float:
    score = base + min(len(set(signals)) * 0.12, 0.5)
    if classifier_match:
        score += 0.18
    return score
