"""Web search tool adapter."""

from __future__ import annotations

from src.utils.helpers import get_logger

log = get_logger(__name__)


class WebSearchTool:
    """Run lightweight web searches for current/external information."""

    def search(self, query: str) -> str:
        """Search the web and return a compact text summary."""
        try:
            from ddgs import DDGS

            log.info("Searching web for query: %s", query)
            results = []
            with DDGS() as ddgs:
                for result in ddgs.text(query, max_results=3):
                    results.append(f"- {result['title']}: {result['body']}")
            if results:
                log.info("Found %d web search result(s).", len(results))
                return "\n".join(results)
            return "No search results found."
        except Exception as error:
            log.warning("Web search failed: %s", error)
            return "Web search unavailable."


def search_web(query: str) -> str:
    """Compatibility helper for legacy callers."""
    return WebSearchTool().search(query)
