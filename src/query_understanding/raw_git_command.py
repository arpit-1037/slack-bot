"""Detection helpers for explicit raw git commands."""

from __future__ import annotations

import shlex


_RAW_GIT_SUBCOMMANDS = frozenset(
    {
        "branch",
        "checkout",
        "commit",
        "diff",
        "fetch",
        "log",
        "merge",
        "pull",
        "push",
        "rebase",
        "reset",
        "revert",
        "show",
        "stash",
        "status",
        "switch",
    }
)


def is_raw_git_command(query: str) -> bool:
    """Return True when the complete query is an explicit supported git command."""
    candidate = (query or "").strip()
    if candidate.startswith("`") and candidate.endswith("`") and len(candidate) >= 2:
        candidate = candidate[1:-1].strip()
    if candidate.startswith("$ "):
        candidate = candidate[2:].strip()

    try:
        parts = shlex.split(candidate)
    except ValueError:
        return False

    if len(parts) < 2 or parts[0].lower() != "git":
        return False
    if any(part in {"&&", "||", ";", "|"} for part in parts):
        return False
    return parts[1].lower() in _RAW_GIT_SUBCOMMANDS
