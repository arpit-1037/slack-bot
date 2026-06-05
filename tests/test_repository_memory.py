"""Testable examples for repository-only memory."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.executor.task_executor import TaskExecutor
from src.memory import (
    MemoryExtractor,
    MemoryRetriever,
    MemoryStore,
    MemoryUpdater,
    MemoryValidator,
    RepositoryFact,
    RepositoryMemory,
    RepositoryMemoryData,
    build_relationship_graph,
)
from src.retrieval import RepositoryRetrievalEngine


class FakeGitTool:
    """Minimal git tool stub for executor memory tests."""

    def __init__(self, repo_path: str) -> None:
        self.repo_path = repo_path


class FakePlanningEngine:
    """Unused planning engine stub."""

    pass


class FakeProviderRouter:
    """Provider fake that should not be called for memory hits."""

    def complete(self, *args, **kwargs) -> str:
        raise AssertionError("Provider should not be called for repository memory hits.")


class FakeExecutionSummary:
    """Execution summary compatible with MemoryUpdater."""

    files_examined = ["src/slack/slack_handler.py"]
    issues_found = ["validation.pytest reported failing tests"]


class RepositoryMemoryTest(unittest.TestCase):
    """Examples for persistence, extraction, retrieval, and integration."""

    def test_store_round_trip_update_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = MemoryStore(str(root), storage_path=root / "memory.json")
            memory = RepositoryMemoryData.empty(str(root))
            fact = RepositoryFact(
                id="fact-1",
                fact_type="architecture",
                key="git logic",
                value="src/tools/git_tool.py",
                confidence=0.96,
                repo_path=str(root),
                file_path="src/tools/git_tool.py",
                tags=["git", "logic"],
            )

            updated = store.update_memory(memory, facts=[fact])
            loaded = store.load_memory()
            deleted = store.delete_memory(fact_ids=["fact-1"])

            self.assertEqual(len(updated.facts), 1)
            self.assertEqual(loaded.facts[0].value, "src/tools/git_tool.py")
            self.assertEqual(deleted.facts, [])

    def test_extracts_repository_facts_and_relationship_graph(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._sample_repo(Path(tmp))

            memory = MemoryExtractor().extract_repository_facts(str(root))
            graph = build_relationship_graph(memory)
            facts = {(fact.key, fact.value) for fact in memory.facts}

            self.assertIn(("application entry point", "app.py"), facts)
            self.assertIn(("framework", "Flask"), facts)
            self.assertIn(("Slack event processing start", "src/slack/slack_handler.py"), facts)
            self.assertTrue(any(fact.key == "git logic" for fact in memory.facts))
            self.assertIn("app.py", graph)

    def test_retrieves_high_confidence_memory_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._sample_repo(Path(tmp))
            store = MemoryStore(str(root), storage_path=root / "memory.json")
            memory = MemoryExtractor().extract_repository_facts(str(root))
            store.save_memory(memory)

            result = MemoryRetriever(store=store).retrieve_memory(
                "Where does Slack processing begin?",
                project_path=str(root),
                min_confidence=0.85,
            )

            self.assertTrue(result.hit)
            self.assertEqual(result.entries[0].fact.value, "src/slack/slack_handler.py")

    def test_validator_marks_missing_file_fact_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("print('hello')\n", encoding="utf-8")
            memory = RepositoryMemoryData(
                repo_path=str(root),
                repo_id="repo",
                facts=[
                    RepositoryFact(
                        id="missing",
                        fact_type="module",
                        key="module missing.py",
                        value="missing.py",
                        confidence=0.8,
                        repo_path=str(root),
                        file_path="missing.py",
                    )
                ],
            )

            validated = MemoryValidator().validate_memory(memory, project_path=str(root))

            self.assertFalse(validated.facts[0].valid)
            self.assertIn("file no longer exists", validated.facts[0].stale_reason)

    def test_store_execution_finding_without_user_conversation_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._sample_repo(Path(tmp))
            store = MemoryStore(str(root), storage_path=root / "memory.json")
            updater = MemoryUpdater(store=store)

            memory = updater.store_execution_finding(FakeExecutionSummary(), str(root))

            self.assertTrue(any(fact.source == "execution_engine" for fact in memory.facts))
            self.assertTrue(any(fact.value == "src/slack/slack_handler.py" for fact in memory.facts))

    def test_retrieval_engine_uses_memory_before_hybrid_retrieval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._sample_repo(Path(tmp))
            store = MemoryStore(str(root), storage_path=root / "memory.json")
            memory = MemoryExtractor().extract_repository_facts(str(root))
            store.save_memory(memory)
            repository_memory = RepositoryMemory(str(root), store=store)
            retrieval = RepositoryRetrievalEngine(
                repository_memory=repository_memory,
                repository_memory_confidence=0.85,
            )

            result = retrieval.retrieve_context(str(root), "Where is git logic?")

            self.assertTrue(result.context.repository_summary["memory"]["hit"])
            self.assertEqual(result.files[0].path, "src/tools/git_tool.py")

    def test_task_executor_answers_repository_lookup_from_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._sample_repo(Path(tmp))
            store = MemoryStore(str(root), storage_path=root / "memory.json")
            memory = MemoryExtractor().extract_repository_facts(str(root))
            store.save_memory(memory)
            repository_memory = RepositoryMemory(str(root), store=store)
            executor = TaskExecutor(
                git_tool=FakeGitTool(str(root)),
                planning_engine=FakePlanningEngine(),
                provider_router=FakeProviderRouter(),
                repository_memory=repository_memory,
            )

            result = executor.execute(
                type(
                    "Plan",
                    (),
                    {
                        "direct_response": None,
                        "run_git_action": False,
                        "return_raw_git_diff": False,
                        "selected_tool_name": None,
                        "selected_tool_input": {},
                        "use_planning_engine": False,
                        "use_execution_engine": False,
                        "use_repository_debugger": False,
                        "use_repository_modifier": False,
                        "needs_git_context": False,
                        "needs_repository_context": True,
                        "needs_web_search": False,
                        "clean_task": "Where is git logic?",
                        "intent": "project_retrieval",
                    },
                )()
            )

            self.assertIn("Repository Memory Hit", result)
            self.assertIn("src/tools/git_tool.py", result)

    def _sample_repo(self, root: Path) -> Path:
        (root / "src" / "slack").mkdir(parents=True)
        (root / "src" / "tools").mkdir(parents=True)
        (root / "src" / "planner").mkdir(parents=True)
        (root / "app.py").write_text(
            "from flask import Flask\n"
            "from src.slack.slack_handler import handle_slack_event\n"
            "app = Flask(__name__)\n",
            encoding="utf-8",
        )
        (root / "src" / "slack" / "slack_handler.py").write_text(
            "def handle_slack_event(event):\n"
            "    return event.get('event_id')\n",
            encoding="utf-8",
        )
        (root / "src" / "tools" / "git_tool.py").write_text(
            "class GitTool:\n"
            "    def get_status(self):\n"
            "        return 'clean'\n",
            encoding="utf-8",
        )
        (root / "src" / "planner" / "task_planner.py").write_text(
            "class TaskPlanner:\n"
            "    pass\n",
            encoding="utf-8",
        )
        return root


if __name__ == "__main__":
    unittest.main()
