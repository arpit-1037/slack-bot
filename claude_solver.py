# claude_solver.py - Groq first, then Gemini, then OpenAI fallback chain
# Git-aware + web search + AI fallback

import logging
import os
import re
import shlex
import subprocess

from ddgs import DDGS
from dotenv import load_dotenv
from google import genai
from google.genai import types
from groq import Groq
from openai import OpenAI

from database import get_conversation_history

load_dotenv()

log = logging.getLogger(__name__)
QUOTE_CHARS = "'\"“”‘’"
CONTINUE_PROMPT = "Continue exactly where you stopped. Do not restart, summarize, or repeat earlier content."
DEFAULT_AI_MAX_OUTPUT_TOKENS = 2048
DEFAULT_OPENAI_MAX_TOKENS = 2048


class AIServiceUnavailableError(RuntimeError):
    """Raised when every configured AI provider fails for a task."""


def int_env(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, default))
    except ValueError:
        value = default
    return max(minimum, value)


def ai_max_output_tokens(provider_env: str, default: int = DEFAULT_AI_MAX_OUTPUT_TOKENS) -> int:
    if os.getenv(provider_env):
        return int_env(provider_env, default)
    return int_env("AI_MAX_OUTPUT_TOKENS", default)


def git_repo_path() -> str:
    path = os.getenv("GIT_REPO_PATH", ".").strip() or "."
    return os.path.abspath(os.path.expanduser(path))


# 1. Clean Slack @mention from task text
def clean_text(task_text: str) -> str:
    return " ".join(
        word for word in task_text.split()
        if not word.startswith("<@")
    ).strip()


# 2. Detect if task is asking about git changes
def is_git_query(task: str) -> bool:
    task_lower = task.lower()
    keywords = [
        "last commit", "changes", "diff", "what changed",
        "committed", "modified", "recent changes", "last committed",
        "what did i change", "show changes", "git", "branch",
        "merge", "stash", "rebase", "pull request", "commit history",
        "status", "staged", "unstaged", "untracked", "working tree",
        "head", "rollback", "revert", "reset", "latest commit",
    ]
    return any(keyword in task_lower for keyword in keywords)


def extract_git_commands(task: str) -> list[list[str]]:
    """Extract explicit `git ...` commands without invoking a shell."""
    commands = []
    for segment in re.split(r"(?:\n|&&|;)", task):
        cleaned = segment.strip().strip("`")
        if cleaned.startswith("$ "):
            cleaned = cleaned[2:].strip()
        git_index = cleaned.find("git ")
        if git_index == -1:
            continue
        cleaned = cleaned[git_index:]
        try:
            parts = shlex.split(cleaned)
        except ValueError:
            continue
        if len(parts) > 1 and parts[0] == "git":
            commands.append(parts[1:])
    return commands


def is_git_action_query(task: str) -> bool:
    task_lower = task.lower()
    if extract_git_commands(task):
        return True

    action_patterns = [
        r"\bstage\b", r"\badd\b", r"\bcommit\b", r"\bpush\b",
        r"\bpull\b", r"\bfetch\b", r"\bstash\b", r"\bmerge\b",
        r"\brebase\b", r"\btag\b", r"\bcheckout\b", r"\bswitch\b",
        r"\brestore\b", r"\breset\b", r"\brevert\b", r"\bcherry[- ]pick\b",
    ]
    read_only_patterns = [
        r"\blast commit\b", r"\bcommit history\b", r"\bshow .*commit\b",
        r"\bwhat .*commit\b", r"\bwhat changed\b", r"\bdiff\b", r"\bstatus\b",
    ]
    return (
        any(re.search(pattern, task_lower) for pattern in action_patterns)
        and not any(re.search(pattern, task_lower) for pattern in read_only_patterns)
    )


# 3. Intent routing
def classify_intent(task: str) -> str:
    task_lower = task.lower().strip()

    greeting_words = {
        "hi", "hello", "hey", "good morning", "good afternoon",
        "good evening", "yo", "hola", "hii", "heyy",
    }
    if task_lower in greeting_words:
        return "greeting"

    if is_git_action_query(task_lower):
        return "git_action"

    if is_git_query(task_lower):
        return "git"

    generic_code_signals = [
        "write code", "code in", "example in", "program in",
        "reverse string", "java code", "python code", "javascript code",
        "c++ code", "php code", "laravel code", "sql query", "regex",
        "algorithm", "function to", "snippet", "syntax",
    ]
    if any(signal in task_lower for signal in generic_code_signals):
        return "generic_code"

    project_debug_signals = [
        "bug", "error", "issue", "not working", "failing", "broken",
        "fix this", "debug", "trace", "exception", "why is this failing",
        "check this code", "review this file",
    ]
    if any(signal in task_lower for signal in project_debug_signals):
        return "project_debug"

    web_signals = [
        "latest", "current", "today", "news", "install", "documentation",
        "docs", "version", "release",
    ]
    if any(signal in task_lower for signal in web_signals):
        return "web"

    return "general"


def greeting_response() -> str:
    return "Hey! Send me a task or question and I will help."


# 4. Git utilities
def run_git_command(args: list[str]) -> str:
    try:
        return subprocess.check_output(
            ["git"] + args,
            stderr=subprocess.DEVNULL,
            cwd=git_repo_path(),
        ).decode().strip()
    except Exception:
        return ""


def run_git_action_command(args: list[str], timeout: int = 120) -> tuple[bool, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(
            ["git"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            env=env,
            cwd=git_repo_path(),
        )
    except subprocess.TimeoutExpired:
        return False, "Command timed out."
    except Exception as error:
        return False, str(error)

    output = "\n".join(
        part.strip() for part in (result.stdout, result.stderr) if part.strip()
    )
    return result.returncode == 0, output or "Command completed."


def has_git_changes(args: list[str]) -> bool:
    try:
        result = subprocess.run(
            ["git"] + args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=git_repo_path(),
        )
    except Exception:
        return False
    return result.returncode == 1


def has_staged_changes() -> bool:
    return has_git_changes(["diff", "--cached", "--quiet"])


def has_worktree_changes() -> bool:
    if has_git_changes(["diff", "--quiet"]):
        return True
    return bool(run_git_command(["ls-files", "--others", "--exclude-standard"]))


def format_git_result(args: list[str], ok: bool, output: str) -> str:
    status = "OK" if ok else "FAILED"
    command = shlex.join(["git"] + args)
    return f"*{status}:* `{command}`\n```\n{output}\n```"


def is_git_repo() -> bool:
    result = run_git_command(["rev-parse", "--is-inside-work-tree"])
    return result.lower() == "true"


def get_default_diff_range() -> tuple[str, str]:
    """
    Decide the safest diff range.
    For repos with at least 2 commits: HEAD~1..HEAD
    For repos with only 1 commit: show HEAD only
    """
    count = run_git_command(["rev-list", "--count", "HEAD"])
    try:
        if int(count) >= 2:
            return "HEAD~1", "HEAD"
    except Exception:
        pass
    return "", "HEAD"


# 5. Return raw git diff directly without AI
def get_raw_diff() -> str:
    if not is_git_repo():
        return f"Could not fetch git diff: configured git project is not a repository: {git_repo_path()}"

    try:
        commits = run_git_command(["log", "--oneline", "-3"]) or "No commits found."
        branch = run_git_command(["branch", "--show-current"]) or "Unknown branch"
        status = run_git_command(["status", "--short"]) or "Working tree clean"

        left, right = get_default_diff_range()

        if left:
            files = run_git_command(["diff", "--name-only", left, right]) or "No files changed."
            diff = run_git_command(["diff", left, right]) or "No diff found."
        else:
            files = run_git_command(["show", "--name-only", "--pretty=format:", right]) or "No files found."
            diff = run_git_command(["show", right, "--format="]) or "No diff found."

        return f"""*Current Branch:*
```
{branch}
```

*Last 3 Commits:*
```
{commits}
```

*Working Tree Status:*
```
{status}
```

*Files Changed:*
```
{files}
```

*Exact Diff:*
```diff
{diff}
```"""
    except Exception as error:
        return f"Could not fetch git diff: {error}"


def extract_commit_message(task: str) -> str:
    patterns = [
        rf"(?:-m|--message)\s+[{QUOTE_CHARS}]([^{QUOTE_CHARS}]+)[{QUOTE_CHARS}]",
        rf"(?:commit message|message|msg)\s*[:=]\s*[{QUOTE_CHARS}]([^{QUOTE_CHARS}]+)[{QUOTE_CHARS}]",
        r"(?:commit message|message|msg)\s*[:=]\s*(.+)$",
        rf"\bwith\s+(?:commit\s+)?(?:message|msg)\s+[{QUOTE_CHARS}]([^{QUOTE_CHARS}]+)[{QUOTE_CHARS}]",
        rf"\bcommit\b.*\b(?:message|msg)\s+[{QUOTE_CHARS}]([^{QUOTE_CHARS}]+)[{QUOTE_CHARS}]",
    ]
    for pattern in patterns:
        match = re.search(pattern, task, re.IGNORECASE)
        if match:
            return match.group(1).strip().strip(": ")
    return ""


def suggest_commit_message() -> str:
    files = run_git_command(["diff", "--cached", "--name-only"]).splitlines()
    if not files:
        files = run_git_command(["diff", "--name-only"]).splitlines()

    if not files:
        return "Update project"
    if len(files) == 1:
        return f"Update {files[0]}"
    if len(files) <= 3:
        return "Update " + ", ".join(files)
    return f"Update {len(files)} files"


def command_has_commit_message(args: list[str]) -> bool:
    return any(
        arg == "-m"
        or arg == "--message"
        or arg.startswith("-m")
        or arg.startswith("--message=")
        or (arg.startswith("-") and not arg.startswith("--") and "m" in arg[1:])
        for arg in args
    )


def normalize_git_command(args: list[str]) -> list[str]:
    if args[:2] == ["commit", "command"]:
        args = ["commit"] + args[2:]
    if args and args[0] == "commit" and not command_has_commit_message(args):
        args = args + ["-m", suggest_commit_message()]
    return args


def run_git_commands(commands: list[list[str]]) -> str:
    if not is_git_repo():
        return f"Could not run git command: configured git project is not a repository: {git_repo_path()}"
    if not commands:
        return "No git command found. Use an explicit command like `git status` or ask me to commit/push changes."

    results = []
    normalized_commands = []
    for args in commands:
        args = normalize_git_command(args)
        if args and args[0] == "commit" and not has_staged_changes() and has_worktree_changes():
            normalized_commands.append(["add", "-A"])
        normalized_commands.append(args)

    for args in normalized_commands:
        ok, output = run_git_action_command(args)
        results.append(format_git_result(args, ok, output))
        if not ok:
            break
    return "\n\n".join(results)


def run_natural_git_action(task: str) -> str:
    task_lower = task.lower()
    commands = []

    wants_commit = bool(re.search(r"\bcommit\b", task_lower))
    wants_push = bool(re.search(r"\bpush\b", task_lower))
    wants_stage = bool(re.search(r"\b(stage|add)\b", task_lower))

    if re.search(r"\bpull\b", task_lower):
        commands.append(["pull"])
    if re.search(r"\bfetch\b", task_lower):
        commands.append(["fetch"])
    if re.search(r"\bstash\b", task_lower):
        commands.append(["stash", "push"])

    if wants_stage or (wants_commit and "staged" not in task_lower):
        commands.append(["add", "-A"])

    if wants_commit:
        message = extract_commit_message(task) or suggest_commit_message()
        commands.append(["commit", "-m", message])

    if wants_push:
        commands.append(["push"])

    return run_git_commands(commands)


def run_git_action(task: str) -> str:
    explicit_commands = extract_git_commands(task)
    if explicit_commands:
        return run_git_commands(explicit_commands)
    return run_natural_git_action(task)


# 6. Git context for AI
def get_git_context() -> str:
    if not is_git_repo():
        return f"GIT CONTEXT: Configured git project is not a repository: {git_repo_path()}"

    context = []
    context.append(f"PROJECT PATH:\n{git_repo_path()}")

    branch = run_git_command(["branch", "--show-current"]) or "Unknown branch"
    context.append(f"CURRENT BRANCH:\n{branch}")

    head_commit = run_git_command(["rev-parse", "--short", "HEAD"]) or "Unavailable"
    context.append(f"CURRENT HEAD:\n{head_commit}")

    recent_commits = run_git_command(["log", "--oneline", "--decorate", "-10"]) or "Git history unavailable."
    context.append(f"LAST 10 COMMITS:\n{recent_commits}")

    staged = run_git_command(["diff", "--cached", "--name-only"])
    context.append(f"STAGED FILES:\n{staged or 'None'}")

    unstaged = run_git_command(["diff", "--name-only"])
    context.append(f"UNSTAGED FILES:\n{unstaged or 'None'}")

    untracked = run_git_command(["ls-files", "--others", "--exclude-standard"])
    context.append(f"UNTRACKED FILES:\n{untracked or 'None'}")

    status = run_git_command(["status", "--short"])
    context.append(f"WORKING TREE STATUS:\n{status or 'Clean'}")

    left, right = get_default_diff_range()

    if left:
        changed_files = run_git_command(["diff", "--name-only", left, right])
        diff_stat = run_git_command(["diff", "--stat", left, right])
        diff = run_git_command(["diff", left, right])
    else:
        changed_files = run_git_command(["show", "--name-only", "--pretty=format:", right])
        diff_stat = run_git_command(["show", "--stat", "--oneline", right])
        diff = run_git_command(["show", right, "--format="])

    context.append(f"FILES CHANGED IN MOST RECENT COMPARISON:\n{changed_files or 'None'}")
    context.append(f"DIFF SUMMARY:\n{diff_stat or 'Unavailable.'}")
    context.append(f"RECENT DIFF:\n{diff or 'Unavailable.'}")

    return "\n\n".join(context)


# 7. Read current codebase
def read_codebase(project_path: str | None = None) -> str:
    project_path = project_path or git_repo_path()
    if not os.path.isdir(project_path):
        return f"Project path not found: {project_path}"

    code_context = []
    for filename in sorted(os.listdir(project_path)):
        if filename.endswith(".py"):
            filepath = os.path.join(project_path, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as file:
                    content = file.read()
                code_context.append(f"=== {filename} ===\n{content}")
            except Exception:
                code_context.append(f"=== {filename} === (could not read)")
    return "\n\n".join(code_context) if code_context else "No Python files found."


# 8. Web search
def search_web(query: str) -> str:
    try:
        print(f"Searching web for: {query}")
        results = []
        with DDGS() as ddgs:
            for result in ddgs.text(query, max_results=3):
                results.append(f"- {result['title']}: {result['body']}")
        if results:
            print(f"Found {len(results)} search results.")
            return "\n".join(results)
        return "No search results found."
    except Exception as error:
        print(f"Web search failed: {error}")
        return "Web search unavailable."


# 9. Build smart prompt
SYSTEM_PROMPT = """You are a direct, no-nonsense coding assistant in Slack.

STRICT RULES:
- Answer ONLY what was asked - nothing more
- If asked for code, paste the ACTUAL code with no paraphrasing
- If the task is a greeting, greet naturally and briefly
- If asked a general question, answer directly
- Only use git context when the task is about git, recent project changes, repo state, or debugging project behavior
- Only use project code when the task is about the current project
- Only use web results when the task needs external or current information
- Never explain your process or steps - just give the answer
- Never say "based on the context..." or "I will now..." - just answer
- Earlier user/assistant turns in this conversation are prior thread history — use them for context when the user refers back to them"""


def build_user_message(
    task: str,
    intent: str,
    git_context: str,
    code_context: str,
    search_context: str,
) -> str:
    return f"""============================
DETECTED INTENT
============================
{intent}

============================
GIT HISTORY & REPO STATE
============================
{git_context}

============================
CURRENT PROJECT CODE
============================
{code_context}

============================
WEB SEARCH RESULTS
============================
{search_context}

============================
TASK
============================
{task}"""


def build_messages(
    task: str,
    thread_ts: str | None,
    channel: str | None,
    slack_user: str | None,
    intent: str,
    git_context: str,
    code_context: str,
    search_context: str,
) -> list[dict]:
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    history = get_conversation_history(thread_ts, channel, slack_user)
    log.info(
        "History lookup: thread_ts=%s channel=%s user=%s -> %d prior turn(s)",
        thread_ts, channel, slack_user, len(history),
    )
    if history:
        print(f"Loaded {len(history)} prior turn(s) of conversation context.")
    for past_task, past_solution in history:
        messages.append({"role": "user", "content": clean_text(past_task)})
        messages.append({"role": "assistant", "content": past_solution})
    messages.append({
        "role": "user",
        "content": build_user_message(task, intent, git_context, code_context, search_context),
    })
    return messages


def needs_continuation(finish_reason) -> bool:
    reason = str(finish_reason or "").lower()
    return reason in {"length", "max_tokens"} or "max_token" in reason


def solve_with_groq(messages: list[dict]) -> str:
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    working_messages = list(messages)
    parts = []

    while True:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=working_messages,
            max_tokens=ai_max_output_tokens("GROQ_MAX_TOKENS"),
        )
        choice = response.choices[0]
        content = choice.message.content or ""
        parts.append(content)

        if not content or not needs_continuation(getattr(choice, "finish_reason", None)):
            break

        working_messages.append({"role": "assistant", "content": content})
        working_messages.append({"role": "user", "content": CONTINUE_PROMPT})

    return "".join(parts)


def solve_with_gemini(messages: list[dict]) -> str:
    # Gemini's generate_content takes a single prompt here; flatten while preserving roles
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    convo_parts = []
    for m in messages:
        if m["role"] == "system":
            continue
        speaker = "User" if m["role"] == "user" else "Assistant"
        convo_parts.append(f"{speaker}: {m['content']}")
    convo = "\n\n".join(convo_parts)
    prompt = f"{system}\n\n{convo}" if system else convo
    parts = []
    current_prompt = prompt

    while True:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=current_prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=ai_max_output_tokens("GEMINI_MAX_OUTPUT_TOKENS"),
            ),
        )
        content = response.text or ""
        parts.append(content)

        candidate = response.candidates[0] if getattr(response, "candidates", None) else None
        finish_reason = getattr(candidate, "finish_reason", None)
        if not content or not needs_continuation(finish_reason):
            break

        current_prompt += f"\n\nAssistant: {content}\n\nUser: {CONTINUE_PROMPT}"

    return "".join(parts)


def solve_with_openai(messages: list[dict]) -> str:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    working_messages = list(messages)
    parts = []

    while True:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=working_messages,
            max_tokens=ai_max_output_tokens("OPENAI_MAX_TOKENS", DEFAULT_OPENAI_MAX_TOKENS),
        )
        choice = response.choices[0]
        content = choice.message.content or ""
        parts.append(content)

        if not content or not needs_continuation(getattr(choice, "finish_reason", None)):
            break

        working_messages.append({"role": "assistant", "content": content})
        working_messages.append({"role": "user", "content": CONTINUE_PROMPT})

    return "".join(parts)


# 11. Main solver
def solve_task(
    task_text: str,
    thread_ts: str | None = None,
    channel: str | None = None,
    slack_user: str | None = None,
) -> str:
    clean = clean_text(task_text)

    if not clean:
        return "I did not receive a task. Please mention me with a task description."

    print(f"\n--- New Task ---\nTask: {clean}")

    if is_git_action_query(clean):
        print("Git action detected before AI routing - running git command.")
        return run_git_action(clean)

    intent = classify_intent(clean)
    print(f"Detected intent: {intent}")

    if intent == "greeting":
        return greeting_response()

    if intent == "git_action":
        print("Git action detected - running git command.")
        return run_git_action(clean)

    if intent == "git":
        print("Git query detected - returning raw diff.")
        return get_raw_diff()

    git_context = "Not needed for this task."
    code_context = "Not needed for this task."
    search_context = "Not needed for this task."

    if intent == "project_debug":
        print("Reading git context...")
        git_context = get_git_context()

        print("Reading codebase...")
        code_context = read_codebase(git_repo_path())
    elif intent == "web":
        print("Searching web...")
        search_context = search_web(clean)

    messages = build_messages(
        clean, thread_ts, channel, slack_user,
        intent, git_context, code_context, search_context,
    )
    provider_errors = []

    try:
        print("Trying Groq...")
        result = solve_with_groq(messages)
        print("Groq responded.")
        return result
    except Exception as error:
        provider_errors.append(f"Groq: {error}")
        print(f"Groq failed: {error}")

    try:
        print("Trying Gemini...")
        result = solve_with_gemini(messages)
        print("Gemini responded.")
        return result
    except Exception as error:
        provider_errors.append(f"Gemini: {error}")
        print(f"Gemini failed: {error}")

    try:
        print("Trying OpenAI...")
        result = solve_with_openai(messages)
        print("OpenAI responded.")
        return result
    except Exception as error:
        provider_errors.append(f"OpenAI: {error}")
        print(f"OpenAI failed: {error}")

    log.error("All AI providers failed: %s", "; ".join(provider_errors))
    raise AIServiceUnavailableError(
        "All configured AI providers failed. Check bot.log for quota, rate-limit, API-key, or payload-size errors."
    )
