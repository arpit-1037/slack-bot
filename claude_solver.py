# claude_solver.py - Groq first, then Gemini, then OpenAI fallback chain
# Git-aware + web search + AI fallback

import os
import subprocess

from ddgs import DDGS
from dotenv import load_dotenv
from google import genai
from groq import Groq
from openai import OpenAI

load_dotenv()


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


# 3. Intent routing
def classify_intent(task: str) -> str:
    task_lower = task.lower().strip()

    greeting_words = {
        "hi", "hello", "hey", "good morning", "good afternoon",
        "good evening", "yo", "hola", "hii", "heyy",
    }
    if task_lower in greeting_words:
        return "greeting"

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


# 4. Git utilities
def run_git_command(args: list[str]) -> str:
    try:
        return subprocess.check_output(
            ["git"] + args,
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return ""


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
        return "Could not fetch git diff: current directory is not a git repository."

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


# 6. Git context for AI
def get_git_context() -> str:
    if not is_git_repo():
        return "GIT CONTEXT: Current directory is not a git repository."

    context = []

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
        diff = run_git_command(["diff", left, right])[:3000]
    else:
        changed_files = run_git_command(["show", "--name-only", "--pretty=format:", right])
        diff_stat = run_git_command(["show", "--stat", "--oneline", right])
        diff = run_git_command(["show", right, "--format="])[:3000]

    context.append(f"FILES CHANGED IN MOST RECENT COMPARISON:\n{changed_files or 'None'}")
    context.append(f"DIFF SUMMARY:\n{diff_stat or 'Unavailable.'}")
    context.append(f"RECENT DIFF:\n{diff or 'Unavailable.'}")

    return "\n\n".join(context)


# 7. Read current codebase
def read_codebase(project_path: str = ".") -> str:
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
def build_prompt(
    task: str,
    intent: str,
    git_context: str,
    code_context: str,
    search_context: str,
) -> str:
    return f"""You are a direct, no-nonsense coding assistant in Slack.

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

============================
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


# 10. AI solvers
def solve_with_groq(prompt: str) -> str:
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def solve_with_gemini(prompt: str) -> str:
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
    )
    return response.text


def solve_with_openai(prompt: str) -> str:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


# 11. Main solver
def solve_task(task_text: str) -> str:
    clean = clean_text(task_text)

    if not clean:
        return "I did not receive a task. Please mention me with a task description."

    print(f"\n--- New Task ---\nTask: {clean}")

    intent = classify_intent(clean)
    print(f"Detected intent: {intent}")

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
        code_context = read_codebase()
    elif intent == "web":
        print("Searching web...")
        search_context = search_web(clean)

    prompt = build_prompt(clean, intent, git_context, code_context, search_context)

    try:
        print("Trying Groq...")
        result = solve_with_groq(prompt)
        print("Groq responded.")
        return result
    except Exception as error:
        print(f"Groq failed: {error}")

    try:
        print("Trying Gemini...")
        result = solve_with_gemini(prompt)
        print("Gemini responded.")
        return result
    except Exception as error:
        print(f"Gemini failed: {error}")

    try:
        print("Trying OpenAI...")
        result = solve_with_openai(prompt)
        print("OpenAI responded.")
        return result
    except Exception as error:
        print(f"OpenAI failed: {error}")

    return "Sorry, all AI services are temporarily unavailable. Please try again in a few minutes."
