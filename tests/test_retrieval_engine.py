"""Testable examples for deterministic repository retrieval."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.planner.task_planner import TaskPlanner
from src.repository.context_selector import ContextSelector
from src.retrieval.retrieval_engine import RepositoryRetrievalEngine


class RepositoryRetrievalEngineTest(unittest.TestCase):
    """Examples for file ranking, symbol ranking, snippets, and dependency expansion."""

    def test_retrieves_relevant_files_symbols_and_dependency_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._write_sample_repository(Path(tmp))
            engine = RepositoryRetrievalEngine(max_files=4, max_symbols=8)

            result = engine.retrieve_context(str(root), "Where is JWT login implemented?")

            paths = [file.path for file in result.files]
            symbol_names = [symbol.name for symbol in result.symbols]
            formatted = result.context.format_context()

            self.assertIn("src/auth.py", paths)
            self.assertIn("src/jwt_service.py", paths)
            self.assertIn("JWTService", symbol_names)
            self.assertIn("authenticate_user", symbol_names)
            self.assertIn("src/auth.py -> src/jwt_service.py", result.context.dependency_edges)
            self.assertIn("REPOSITORY RETRIEVAL CONTEXT", formatted)
            self.assertIn("Ranking decisions", formatted)
            self.assertTrue(result.context.snippets)

    def test_context_selector_returns_legacy_selection_from_retrieval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._write_sample_repository(Path(tmp))
            selector = ContextSelector(max_files=3)

            selection = selector.select_context(str(root), "Which file handles Slack events?")

            paths = [file.path for file in selection.selected_files]
            self.assertIn("src/slack_handler.py", paths)
            self.assertIn("REPOSITORY RETRIEVAL CONTEXT", selection.context)
            self.assertIn("handle_slack_event", selection.context)

    def _write_sample_repository(self, root: Path) -> Path:
        """Create a tiny repository with auth, JWT, and Slack event code."""
        source = root / "src"
        source.mkdir()
        (source / "__init__.py").write_text("", encoding="utf-8")
        (source / "jwt_service.py").write_text(
            "class JWTService:\n"
            "    \"\"\"Create and verify JWT tokens.\"\"\"\n"
            "\n"
            "    def create_token(self, username):\n"
            "        return f'jwt:{username}'\n"
            "\n"
            "    def verify_token(self, token):\n"
            "        return token.startswith('jwt:')\n",
            encoding="utf-8",
        )
        (source / "auth.py").write_text(
            "from src.jwt_service import JWTService\n"
            "\n"
            "\n"
            "def authenticate_user(username, password):\n"
            "    \"\"\"Authenticate login credentials and return a JWT.\"\"\"\n"
            "    if not password:\n"
            "        return None\n"
            "    return JWTService().create_token(username)\n",
            encoding="utf-8",
        )
        (source / "slack_handler.py").write_text(
            "def handle_slack_event(event):\n"
            "    \"\"\"Handle Slack events from the Events API.\"\"\"\n"
            "    return event.get('type')\n",
            encoding="utf-8",
        )
        return root


class RetrievalRoutingTest(unittest.TestCase):
    """Examples for routing repository lookup questions to retrieval context."""

    def test_repository_lookup_question_needs_repository_context(self) -> None:
        plan = TaskPlanner().create_plan("Where is JWT implemented?")

        self.assertEqual(plan.intent, "project_retrieval")
        self.assertTrue(plan.needs_repository_context)
        self.assertFalse(plan.use_repository_debugger)
        self.assertFalse(plan.use_repository_modifier)


if __name__ == "__main__":
    unittest.main()
