"""Deterministic typo, shorthand, and spacing normalization."""

from __future__ import annotations

import re
from difflib import SequenceMatcher


_PHRASE_REPAIRS = {
    r"\bgivem\s+e\b": "give me",
    r"\bgive\s+m\s+e\b": "give me",
    r"\bgive\s+me\s+the\s+lst\b": "give me the list",
    r"\bcheckout\s+(why|this|that|the)\b": r"check \1",
    r"\bcheck\s+out\s+(why|this|that|the)\b": r"check \1",
}

_TOKEN_REPAIRS = {
    "lst": "list",
    "braches": "branches",
    "brnaches": "branches",
    "branchs": "branches",
    "waht": "what",
    "giv": "give",
    "func": "function",
    "fn": "function",
    "cfg": "configuration",
    "conf": "configuration",
    "repo": "repository",
    "repos": "repositories",
    "proj": "project",
    "svc": "service",
    "srvc": "service",
    "authn": "authentication",
    "authz": "authorization",
    "env": "environment",
    "deps": "dependencies",
    "impl": "implementation",
    "commits": "commits",
    "oif": "of",
}

_KNOWN_DEVELOPER_TERMS = {
    "authentication",
    "authorization",
    "branch",
    "branches",
    "codebase",
    "commit",
    "commits",
    "configuration",
    "controller",
    "database",
    "dependencies",
    "diff",
    "function",
    "handler",
    "history",
    "implementation",
    "module",
    "project",
    "repository",
    "service",
    "slack",
    "status",
    "testing",
}


def normalize_query(query: str) -> tuple[str, str]:
    """Return the original query and a normalized query for routing."""
    original = query or ""
    normalized = _normalize_spacing(original)
    normalized = _repair_phrases(normalized)
    normalized = _repair_split_tokens(normalized)
    normalized = _repair_tokens(normalized)
    normalized = _normalize_spacing(normalized)
    return original, normalized


def _normalize_spacing(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _repair_phrases(value: str) -> str:
    repaired = value
    for pattern, replacement in _PHRASE_REPAIRS.items():
        repaired = re.sub(pattern, replacement, repaired, flags=re.IGNORECASE)
    return repaired


def _repair_split_tokens(value: str) -> str:
    tokens = value.split()
    if not tokens:
        return value

    repaired: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if index + 1 < len(tokens):
            joined = _word_only(token + tokens[index + 1])
            if joined in _KNOWN_DEVELOPER_TERMS or joined in {"me", "you"}:
                repaired.append(_match_case(token + tokens[index + 1], joined))
                index += 2
                continue
        repaired.append(token)
        index += 1
    return " ".join(repaired)


def _repair_tokens(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        word = match.group(0)
        lower = word.lower()
        repaired = _TOKEN_REPAIRS.get(lower)
        if repaired is None and len(lower) >= 5:
            repaired = _closest_known_term(lower)
        return _match_case(word, repaired) if repaired else word

    return re.sub(r"\b[A-Za-z][A-Za-z0-9_+-]*\b", replace, value)


def _closest_known_term(word: str) -> str | None:
    best_term = ""
    best_ratio = 0.0
    for term in _KNOWN_DEVELOPER_TERMS:
        ratio = SequenceMatcher(None, word, term).ratio()
        if ratio > best_ratio:
            best_term = term
            best_ratio = ratio
    if best_ratio >= 0.84:
        return best_term
    return None


def _word_only(value: str) -> str:
    return re.sub(r"[^a-z0-9_+-]", "", value.lower())


def _match_case(source: str, replacement: str) -> str:
    if source.isupper():
        return replacement.upper()
    if source[:1].isupper():
        return replacement.capitalize()
    return replacement
