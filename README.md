python3 app.py 

ngrok http 3000
# 🤖 Slack Claude Bot

Automatically solves tasks when you're @mentioned in Slack, powered by a Groq → Gemini → OpenAI fallback chain.

## How It Works

```
@mention in Slack → Flask Server → Slack handler → Planner → Executor/tools → LLM provider fallback → Reply in thread + Save to DB
```

---

## 📁 Project Structure

```
slack-claude-bot/
├── app.py                         ← Lightweight Flask entrypoint
├── claude_solver.py               ← Backward-compatible solver facade
├── database.py                    ← Saves tasks & solutions to SQLite
├── src/
│   ├── slack/                     ← Slack request handling and responses
│   ├── router/                    ← Intent classification
│   ├── planner/                   ← Deterministic task planning
│   ├── executor/                  ← Tool execution and orchestration
│   ├── tools/                     ← Git, repository, web, terminal, conversation tools
│   ├── repository/                ← Recursive repository scanner
│   ├── modification/              ← Safe targeted repository modification
│   ├── llm/                       ← Provider fallback and continuation handling
│   ├── prompts/                   ← System prompt and prompt builder
│   ├── memory/                    ← Conversation history access
│   └── utils/                     ← Small shared helpers
├── requirements.txt
├── .env.example
└── README.md
```

---

## 🚀 Setup (Step by Step)

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Set Up Your Environment

```bash
cp .env.example .env
```

Open `.env` and fill in:

```
SLACK_BOT_TOKEN=xoxb-...        ← From Slack App → OAuth & Permissions
SLACK_SIGNING_SECRET=...        ← From Slack App → Basic Information
GROQ_API_KEY=gsk-...
GEMINI_API_KEY=...
ANTHROPIC_API_KEY=sk-ant-...    ← Claude/Anthropic API key
OPENAI_API_KEY=sk-...
AI_PROVIDER_ORDER=gemini        ← Use only Gemini for bot responses
GEMINI_MODEL=gemini-2.5-flash
GIT_REPO_PATH=/absolute/path/to/project
```

### 3. Create Your Slack App

1. Go to https://api.slack.com/apps → **Create New App → From Scratch**
2. **OAuth & Permissions** → Add Bot Token Scopes:
   - `app_mentions:read`
   - `chat:write`
   - `channels:history`
3. Click **Install to Workspace** → copy the `xoxb-...` token into `.env`
4. **Basic Information** → copy **Signing Secret** into `.env`

### 4. Run the Bot

```bash
python app.py
```

### 5. Expose to Internet (for local testing)

```bash
# In a new terminal
ngrok http 3000
# Copy the https URL e.g. https://abc123.ngrok.io
```

### 6. Connect Slack to Your Server

1. In your Slack App → **Event Subscriptions** → Enable Events → ON
2. Paste Request URL: `https://abc123.ngrok.io/slack/events`
3. Wait for ✅ Verified
4. Under **Subscribe to Bot Events** → Add `app_mention`
5. Click **Save Changes**

### 7. Add Bot to a Channel

In Slack, go to any channel and type:
```
/invite @YourBotName
```

---

## 💬 Usage

Mention the bot with any task:

```
@TaskBot write a SQL query to find all users who signed up last month
@TaskBot explain how to fix a CORS error in Flask
@TaskBot create a Python function to validate email addresses
```

The bot will:
1. Acknowledge immediately: `⏳ On it!`
2. Plan the task and execute only the needed tools
3. Reply in the thread with the solution
4. Save everything to `tasks.db`

---

## 🗄️ View Saved Tasks

```python
from database import get_all_tasks
for task in get_all_tasks():
    print(dict(task))
```

Or open `tasks.db` in **TablePlus** or **DBeaver** to browse visually.

---

## 📋 Logs

All activity is logged to `bot.log`, including request lifecycle ids:

```bash
tail -f bot.log
```

## Repository-Aware Debugging

Debugging tasks now use a focused repository workflow instead of sending a broad code dump:

```
bug report
  ↓
StacktraceParser
  ↓
BugContextBuilder
  ↓
RepositoryIndexer + DependencyMapper + ContextSelector
  ↓
DebugPromptBuilder
  ↓
ProviderRouter
```

Useful local examples:

```python
from src.debugging.stacktrace_parser import StacktraceParser

trace = StacktraceParser().parse(user_text)
print(trace.as_dict())
```

```python
from src.debugging.bug_context_builder import BugContextBuilder
from src.debugging.stacktrace_parser import StacktraceParser

trace = StacktraceParser().parse("Why JWT login failing?")
context = BugContextBuilder().build(".", "Why JWT login failing?", trace)
print(context.format_context())
```

Debug context limits:

```
DEBUG_CONTEXT_MAX_FILES=5
DEBUG_SNIPPET_RADIUS=14
DEBUG_CONTEXT_MAX_FILE_CHARS=5000
```

## Repository State Management

Repository-aware modules share a persistent state layer so they can reuse fresh repository metadata before falling back to heavier scans:

```
request
  ↓
RepositoryStateRefresher
  ↓
RepositoryStateCache
  ↓
RepositoryIndexer + DependencyMapper + ContextSelector
```

The state tracks branch, HEAD commit, indexed timestamps, supported file counts, Python file counts, changed files, staged files, untracked files, and health signals.

Useful local examples:

```python
from src.repository.state_refresher import RepositoryStateRefresher

state = RepositoryStateRefresher().refresh_state(".")
print(state.get_repository_summary())
```

```python
from src.repository.repository_indexer import RepositoryIndexer

indexer = RepositoryIndexer()
indexer.ensure_index(".")
print(indexer.repository_state.as_summary_dict())
```

Cache controls:

```
REPOSITORY_STATE_CACHE_TTL_SECONDS=300
REPOSITORY_STATE_CACHE_PATH=/absolute/path/to/state.json
REPOSITORY_STATE_CACHE_DIR=/absolute/cache/dir
```

## Safe Repository Modification

Modification tasks use an explicit guarded workflow instead of naive file rewriting:

```
modification request
  ↓
RepositoryIndexer + ContextSelector
  ↓
PatchGenerator structured operations
  ↓
DiffManager preview
  ↓
ChangeValidator syntax/import checks
  ↓
SafeFileEditor atomic apply + backup
```

Useful local examples:

```python
from src.modification.patch_generator import PatchGenerator, PatchOperation

original = {"example.py": "def greet():\n    return 'hi'\n"}
operation = PatchOperation(
    op="replace",
    path="example.py",
    target_type="function",
    target="greet",
    content="def greet():\n    return 'hello'\n",
)
print(PatchGenerator().apply_operations(original, [operation])["example.py"])
```

```python
from src.modification.change_validator import ChangeValidator

result = ChangeValidator().validate(".", {"example.py": "def ok():\n    return True\n"})
print(result.format_report())
```

Optional validation:

```
MODIFICATION_RUN_PYTEST=true
```

---

## ⚠️ Common Issues

| Problem | Fix |
|---|---|
| Request URL not verified | Make sure `python app.py` AND `ngrok` are both running |
| Bot not responding | Run `/invite @YourBot` in the channel |
| Token errors | Reinstall app to workspace after adding new scopes |
| ngrok URL changed | Restart ngrok → update URL in Slack Event Subscriptions |

---

## 🚢 Deploy to Production

Replace ngrok with a real server:

- **Railway** — `railway up` (free tier available)
- **Render** — connect GitHub repo, auto-deploy
- **VPS** — any Ubuntu server with `gunicorn app:app`
