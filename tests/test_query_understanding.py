"""Examples for query understanding and thread-aware tool routing."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.executor.task_executor import TaskExecutor
from src.planner.task_planner import TaskPlanner
from src.query_understanding import normalize_query, route_query, score_intent_confidence
from src.tools.git_tool import GitTool


class QueryNormalizationTest(unittest.TestCase):
    """Examples for deterministic typo and shorthand repair."""

    def test_normalizes_spelling_spacing_and_developer_shorthand(self) -> None:
        original, normalized = normalize_query("givem e the lst of braches in repo")

        self.assertEqual(original, "givem e the lst of braches in repo")
        self.assertEqual(normalized, "give me the list of branches in repository")


class SemanticRoutingTest(unittest.TestCase):
    """Examples for routing equivalent requests to the same tool."""

    def test_branch_phrases_route_to_git_branch_tool(self) -> None:
        for query in [
            "show branches",
            "list branches",
            "what branches exist",
            "display branch list",
            "give me branch names",
        ]:
            with self.subTest(query=query):
                result = route_query(query)

                self.assertEqual(result.intent, "git")
                self.assertEqual(result.tool_name, "git.branch")
                self.assertGreaterEqual(result.confidence, 0.9)

    def test_commit_history_phrases_route_to_git_log_tool(self) -> None:
        for query in ["show commits", "recent commits", "latest commits", "git history"]:
            with self.subTest(query=query):
                result = route_query(query)

                self.assertEqual(result.tool_name, "git.log")

    def test_confidence_scores_git_above_general_for_branch_request(self) -> None:
        results = score_intent_confidence("give me the list of branches")

        self.assertEqual(results[0].intent, "git")
        self.assertGreater(results[0].confidence, 0.65)


class PlannerUnderstandingIntegrationTest(unittest.TestCase):
    """Examples for planner integration before legacy intent classification."""

    def test_typo_branch_request_selects_git_branch_tool(self) -> None:
        plan = TaskPlanner().create_plan(
            "<@U123> give me the lst of braches in our project",
            thread_ts="thread-typo-branch",
            channel="C-query-understanding",
            slack_user="U-query-understanding",
        )

        self.assertEqual(plan.clean_task, "give me the list of branches in our project")
        self.assertEqual(plan.intent, "git")
        self.assertEqual(plan.selected_tool_name, "git.branch")
        self.assertFalse(plan.return_raw_git_diff)
        self.assertFalse(plan.run_git_action)

    def test_retry_followup_inherits_previous_branch_tool(self) -> None:
        planner = TaskPlanner()
        planner.create_plan(
            "show branches",
            thread_ts="thread-followup-retry",
            channel="C-query-understanding",
            slack_user="U-query-understanding",
        )

        followup = planner.create_plan(
            "no give me the exact list after running this",
            thread_ts="thread-followup-retry",
            channel="C-query-understanding",
            slack_user="U-query-understanding",
        )

        self.assertTrue(followup.query_analysis.followup.is_followup)
        self.assertEqual(followup.clean_task, "show branches")
        self.assertEqual(followup.selected_tool_name, "git.branch")

    def test_why_followup_inherits_git_topic_without_running_diff(self) -> None:
        planner = TaskPlanner()
        planner.create_plan(
            "show branches",
            thread_ts="thread-followup-why",
            channel="C-query-understanding",
            slack_user="U-query-understanding",
        )

        followup = planner.create_plan(
            "why?",
            thread_ts="thread-followup-why",
            channel="C-query-understanding",
            slack_user="U-query-understanding",
        )

        self.assertTrue(followup.query_analysis.followup.is_followup)
        self.assertTrue(followup.needs_git_context)
        self.assertFalse(followup.return_raw_git_diff)
        self.assertIsNone(followup.selected_tool_name)


class ToolExecutionIntegrationTest(unittest.TestCase):
    """Examples for executing selected read-only tools."""

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_branch_tool_route_returns_actual_branches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._git(root, ["init"])
            self._git(root, ["config", "user.email", "test@example.com"])
            self._git(root, ["config", "user.name", "Test User"])
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            self._git(root, ["add", "app.py"])
            self._git(root, ["commit", "-m", "initial"])
            self._git(root, ["checkout", "-b", "jun_2"])
            self._git(root, ["branch", "feature"])

            plan = TaskPlanner().create_plan(
                "give me the lst of braches in our project",
                thread_ts="thread-exec-branch",
                channel="C-query-understanding",
                slack_user="U-query-understanding",
            )
            result = TaskExecutor(git_tool=GitTool(repo_path=str(root))).execute(plan)

            self.assertIn("*Current Branch:*", result)
            self.assertIn("jun_2", result)
            self.assertIn("feature", result)
            self.assertNotIn("I cannot access your local Git repository directly", result)

    def _git(self, cwd: Path, args: list[str]) -> None:
        subprocess.run(
            ["git"] + args,
            cwd=cwd,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


if __name__ == "__main__":
    unittest.main()
