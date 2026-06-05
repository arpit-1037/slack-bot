"""Static text for repository-memory responses and logs."""

MEMORY_HIT_TITLE = "*Repository Memory Hit*"
MEMORY_MISS_TITLE = "*Repository Memory Miss*"
REPOSITORY_MEMORY_SCOPE = "Repository Memory stores repository facts only, not user memory or Slack conversations."

MEMORY_OBSERVABILITY_FIELDS = (
    "memory_hit",
    "memory_miss",
    "confidence",
    "updates",
    "invalidations",
    "refreshes",
)
