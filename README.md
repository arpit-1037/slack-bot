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
OPENAI_API_KEY=sk-...
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

## Repository Intelligence

Project-debug tasks now use a deterministic repository intelligence layer instead of loading the whole repository. The flow is:

```
RepositoryScanner → RepositoryIndexer → DependencyMapper → ContextSelector
```

The scanner returns structured file metadata, the indexer extracts Python symbols with `ast`, the dependency mapper links imports to files, and the context selector chooses a small task-relevant context block for the prompt.

Useful limits:

```
REPOSITORY_SCAN_MAX_FILE_BYTES=200000
REPOSITORY_CONTEXT_MAX_FILES=6
REPOSITORY_CONTEXT_MAX_CHARS=24000
REPOSITORY_CONTEXT_MAX_FILE_CHARS=6000
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
