# claude_solver.py — Groq first, then Gemini, then OpenAI fallback chain
# Git-Aware + Web Search + AI Fallback

import os
import subprocess
from google import genai
from openai import OpenAI
from groq import Groq
from dotenv import load_dotenv
from ddgs import DDGS

load_dotenv()


# ── 1. Clean Slack @mention from task text ─────────────────────────────────────
def clean_text(task_text: str) -> str:
    return " ".join(
        word for word in task_text.split()
        if not word.startswith("<@")
    ).strip()


# ── 2. Detect if task is asking about git changes ──────────────────────────────
def is_git_query(task: str) -> bool:
    keywords = [
        "last commit", "changes", "diff", "what changed",
        "committed", "modified", "recent changes", "last committed",
        "what did i change", "show changes", "git"
    ]
    return any(k in task.lower() for k in keywords)


# ── 3. Return raw git diff directly without AI ────────────────────────────────
def get_raw_diff() -> str:
    try:
        commits = subprocess.check_output(
            ["git", "log", "--oneline", "-3"],
            stderr=subprocess.DEVNULL
        ).decode().strip()

        files = subprocess.check_output(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
            stderr=subprocess.DEVNULL
        ).decode().strip()

        diff = subprocess.check_output(
            ["git", "diff", "HEAD~1", "HEAD"],
            stderr=subprocess.DEVNULL
        ).decode().strip()

        return f"""*Last 3 Commits:*
```
{commits}
```

*Files Changed:*
```
{files}
```

*Exact Diff:*
```diff
{diff}
```"""

    except Exception as e:
        return f"Could not fetch git diff: {e}"


# ── 4. Git Context for AI ──────────────────────────────────────────────────────
def get_git_context() -> str:
    context = []

    try:
        commits = subprocess.check_output(
            ["git", "log", "--oneline", "-5"],
            stderr=subprocess.DEVNULL
        ).decode().strip()
        context.append(f"LAST 5 COMMITS:\n{commits}")
    except Exception:
        context.append("COMMITS: Git history unavailable.")

    try:
        changed_files = subprocess.check_output(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
            stderr=subprocess.DEVNULL
        ).decode().strip()
        context.append(f"FILES CHANGED IN LAST COMMIT:\n{changed_files}")
    except Exception:
        context.append("CHANGED FILES: Unavailable (possibly first commit).")

    try:
        diff = subprocess.check_output(
            ["git", "diff", "HEAD~1", "HEAD"],
            stderr=subprocess.DEVNULL
        ).decode().strip()[:3000]
        context.append(f"RECENT DIFF:\n{diff}")
    except Exception:
        context.append("DIFF: Unavailable.")

    try:
        uncommitted = subprocess.check_output(
            ["git", "status", "--short"],
            stderr=subprocess.DEVNULL
        ).decode().strip()
        if uncommitted:
            context.append(f"CURRENTLY MODIFIED (UNCOMMITTED):\n{uncommitted}")
        else:
            context.append("UNCOMMITTED CHANGES: None — working tree is clean.")
    except Exception:
        context.append("UNCOMMITTED: Unavailable.")

    return "\n\n".join(context)


# ── 5. Read Current Codebase ───────────────────────────────────────────────────
def read_codebase(project_path: str = ".") -> str:
    code_context = []
    for filename in sorted(os.listdir(project_path)):
        if filename.endswith(".py"):
            filepath = os.path.join(project_path, filename)
            try:
                with open(filepath, "r") as f:
                    content = f.read()
                code_context.append(f"=== {filename} ===\n{content}")
            except Exception:
                code_context.append(f"=== {filename} === (could not read)")
    return "\n\n".join(code_context) if code_context else "No Python files found."


# ── 6. Web Search ──────────────────────────────────────────────────────────────
def search_web(query: str) -> str:
    try:
        print(f"Searching web for: {query}")
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=3):
                results.append(f"- {r['title']}: {r['body']}")
        if results:
            print(f"Found {len(results)} search results.")
            return "\n".join(results)
        return "No search results found."
    except Exception as e:
        print(f"Web search failed: {e}")
        return "Web search unavailable."


# ── 7. Build Smart Prompt ──────────────────────────────────────────────────────
def build_prompt(task: str, git_context: str, code_context: str, search_context: str) -> str:
    return f"""You are a direct, no-nonsense coding assistant in Slack.

STRICT RULES:
- Answer ONLY what was asked — nothing more
- If asked for code → paste the ACTUAL code, no paraphrasing
- If asked a general question → answer directly, ignore git/code context
- Only reference git history or code when the task is specifically about code or bugs
- Never explain your process or steps — just give the answer
- Never say "based on the context..." or "I will now..." — just answer

============================
GIT HISTORY & RECENT CHANGES
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


# ── 8. AI Solvers ──────────────────────────────────────────────────────────────
def solve_with_groq(prompt: str) -> str:
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content


def solve_with_gemini(prompt: str) -> str:
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )
    return response.text


def solve_with_openai(prompt: str) -> str:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content


# ── 9. Main Solver ─────────────────────────────────────────────────────────────
def solve_task(task_text: str) -> str:
    clean = clean_text(task_text)

    if not clean:
        return "I did not receive a task. Please mention me with a task description."

    print(f"\n--- New Task ---\nTask: {clean}")

    # If asking about git changes — return raw diff directly, skip AI
    if is_git_query(clean):
        print("Git query detected — returning raw diff.")
        return get_raw_diff()

    # Gather all context for AI
    print("Reading git context...")
    git_context = get_git_context()

    print("Reading codebase...")
    code_context = read_codebase()

    print("Searching web...")
    search_context = search_web(clean)

    # Build smart prompt
    prompt = build_prompt(clean, git_context, code_context, search_context)

    # Try Groq FIRST (free, generous limits)
    try:
        print("Trying Groq...")
        result = solve_with_groq(prompt)
        print("Groq responded.")
        return result
    except Exception as e:
        print(f"Groq failed: {e}")

    # Fallback to Gemini
    try:
        print("Trying Gemini...")
        result = solve_with_gemini(prompt)
        print("Gemini responded.")
        return result
    except Exception as e:
        print(f"Gemini failed: {e}")

    # Fallback to OpenAI
    try:
        print("Trying OpenAI...")
        result = solve_with_openai(prompt)
        print("OpenAI responded.")
        return result
    except Exception as e:
        print(f"OpenAI failed: {e}")

    # All failed
    return "Sorry, all AI services are temporarily unavailable. Please try again in a few minutes."