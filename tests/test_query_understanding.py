"""Examples for query understanding and thread-aware tool routing."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.executor.task_executor import TaskExecutor
from src.planner.task_planner import TaskPlanner
from src.query_understanding import (
    ConversationState,
    FollowupResolver,
    is_raw_git_command,
    normalize_query,
    route_query,
    score_intent_confidence,
)
from src.tools.git_tool import GitTool, extract_git_commands


class QueryNormalizationTest(unittest.TestCase):
    """Examples for deterministic typo and shorthand repair."""

    def test_normalizes_spelling_spacing_and_developer_shorthand(self) -> None:
        original, normalized = normalize_query("givem e the lst of braches in repo")

        self.assertEqual(original, "givem e the lst of braches in repo")
        self.assertEqual(normalized, "give me the list of branches in repository")


class RawGitCommandTest(unittest.TestCase):
    """Raw git commands remain isolated from conversation follow-up context."""

    RAW_COMMANDS = [
        "git log",
        "git status",
        "git diff",
        "git branch",
        "git show",
        "git checkout",
        "git switch",
        "git commit",
        "git push",
        "git pull",
        "git fetch",
        "git merge",
        "git rebase",
        "git stash",
        "git reset",
        "git revert",
        "git branch -a",
        "git diff HEAD~1 HEAD",
    ]

    def test_detects_supported_raw_git_commands(self) -> None:
        for query in self.RAW_COMMANDS:
            with self.subTest(query=query):
                self.assertTrue(is_raw_git_command(query))

    def test_does_not_classify_conversational_requests_as_raw_git_commands(self) -> None:
        for query in ["why?", "show me that code", "try again", "what about that file?"]:
            with self.subTest(query=query):
                self.assertFalse(is_raw_git_command(query))

    def test_raw_git_commands_are_never_modified_by_followup_resolver(self) -> None:
        resolver = FollowupResolver()
        state = ConversationState(
            thread_key="raw-git-test",
            active_topic="git",
            active_repository_task="<@U123> show recent commits",
            active_tool_name="git.log",
            last_normalized_query="<@U123> show recent commits",
            last_resolved_query="<@U123> show recent commits",
            last_tool_name="git.log",
        )

        for query in self.RAW_COMMANDS:
            with self.subTest(query=query):
                result = resolver.resolve_followup(query, state)

                self.assertEqual(result.original_query, query)
                self.assertEqual(result.resolved_query, query)
                self.assertFalse(result.is_followup)
                self.assertTrue(result.raw_git_command_detected)
                self.assertEqual(result.inherited_topic, "")
                self.assertEqual(result.inherited_tool_name, "")

    def test_real_followups_still_use_conversation_context(self) -> None:
        resolver = FollowupResolver()
        state = ConversationState(
            thread_key="real-followup-test",
            active_topic="repository",
            active_repository_task="inspect src/router/intent_router.py",
            active_tool_name="repository.file_search",
            last_resolved_query="inspect src/router/intent_router.py",
        )

        for query in ["why?", "show me that code", "try again", "what about that file?"]:
            with self.subTest(query=query):
                result = resolver.resolve_followup(query, state)

                self.assertTrue(result.is_followup)
                self.assertFalse(result.raw_git_command_detected)
                self.assertIn("src/router/intent_router.py", result.resolved_query)


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

    def test_raw_git_command_bypasses_existing_thread_context(self) -> None:
        planner = TaskPlanner()
        thread = "thread-raw-git-command"
        planner.create_plan(
            "show branches",
            thread_ts=thread,
            channel="C-query-understanding",
            slack_user="U-query-understanding",
        )

        with self.assertLogs("src.planner.task_planner", level="INFO") as logs:
            plan = planner.create_plan(
                "git log",
                thread_ts=thread,
                channel="C-query-understanding",
                slack_user="U-query-understanding",
                request_id="raw-git-test",
            )

        self.assertEqual(plan.query_analysis.original_query, "git log")
        self.assertEqual(plan.query_analysis.resolved_query, "git log")
        self.assertFalse(plan.query_analysis.followup.is_followup)
        self.assertEqual(plan.query_analysis.followup.inherited_topic, "")
        self.assertEqual(plan.query_analysis.topic.previous_topic, "")
        self.assertEqual(plan.clean_task, "git log")
        self.assertEqual(plan.intent, "git_action")
        self.assertEqual(extract_git_commands(plan.clean_task), [["log"]])
        self.assertTrue(
            any("raw_git_command_detected=True" in message for message in logs.output)
        )


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
