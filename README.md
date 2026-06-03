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
│   ├── retrieval/                 ← Deterministic file/symbol/snippet retrieval
│   ├── hybrid_retrieval/          ← Keyword + dependency + semantic + git score fusion
│   ├── embeddings/                ← Semantic chunks, embeddings, vector search
│   ├── modification/              ← Safe targeted repository modification
│   ├── validation/                ← Syntax, import, test, lint verification
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

Optional semantic retrieval backends:

```bash
pip install sentence-transformers chromadb "Pillow>=9.1.0"
```

Then enable vector search:

```bash
RETRIEVAL_ENABLE_SEMANTIC=true
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

## Repository Retrieval Engine

Repository-aware questions use a deterministic retrieval layer before any LLM call. The public entrypoint stays `RepositoryRetrievalEngine`, and it now delegates ranking to `HybridRetriever` so all retrieval signals are fused before context assembly:

```
user question
  ->
RepositoryRetrievalEngine
  ->
HybridRetriever
  ->
FileRanker keyword signal
  ->
DependencyMapper graph signal
  ->
optional EmbeddingIndexBuilder semantic signal
  ->
RepositoryState git signal
  ->
ScoreFusion + RetrievalRanker
  ->
ContextAssembler focused snippets
  ->
LLM prompt context
```

This layer ranks files, symbols, and snippets with path, symbol, import, dependency, working-tree, recent git history, and optional semantic signals. It does not use LangChain, LangGraph, autonomous planning, or autonomous code modification.

Useful local examples:

```python
from src.retrieval import RepositoryRetrievalEngine

engine = RepositoryRetrievalEngine()
result = engine.retrieve_context(".", "Where is JWT implemented?")

for file in result.files:
    print(file.path, file.score, file.reasons)

for symbol in result.symbols:
    print(symbol.name, symbol.kind, symbol.file_path, symbol.score)

print(result.context.format_context())
```

Hybrid score-fusion example:

```python
from src.hybrid_retrieval import HybridRetriever

retriever = HybridRetriever(enable_semantic_search=True)
result = retriever.retrieve(".", "Where do we generate temporary login links?")

for line in result.explanations:
    print(line)
```

Default hybrid weights:

```
semantic: 40%
dependency: 30%
keyword: 20%
git: 10%
```

Questions that benefit from retrieval:

```
Where is JWT implemented?
Which file handles Slack events?
What service performs authentication?
Which files are related to Redis?
What changed login behavior?
```

Retrieval limits:

```
RETRIEVAL_MAX_FILES=6
RETRIEVAL_MAX_SYMBOLS=12
RETRIEVAL_DEPENDENCY_LIMIT=2
RETRIEVAL_MAX_CONTEXT_CHARS=24000
RETRIEVAL_SNIPPET_RADIUS=8
RETRIEVAL_ENABLE_SEMANTIC=false
EMBEDDING_SEARCH_LIMIT=6
HYBRID_RETRIEVAL_GIT_HISTORY_LIMIT=20
```

## Embeddings & Vector Search

Semantic retrieval is additive infrastructure. It chunks code into meaningful units, embeds those chunks, stores vectors, and returns semantic matches that can be merged into normal repository retrieval.

```
RepositoryIndexer
  ->
CodeChunker
  ->
EmbeddingService
  ->
VectorStore
  ->
EmbeddingIndexBuilder
  ->
RepositoryRetrievalEngine
```

The embedding service lazily uses `sentence-transformers` with `all-MiniLM-L6-v2` when available. If the package or model is unavailable, it falls back to deterministic hash embeddings so local tests and offline development still work. The vector store uses ChromaDB when installed and falls back to in-memory cosine search otherwise.

Chunking example:

```python
from src.repository.repository_indexer import RepositoryIndexer
from src.embeddings import CodeChunker

index = RepositoryIndexer().ensure_index(".")
chunks = CodeChunker().chunk_repository(index)

for chunk in chunks[:5]:
    print(chunk.file_path, chunk.symbol_name, chunk.chunk_type)
```

Semantic search example:

```python
from src.embeddings import EmbeddingIndexBuilder

builder = EmbeddingIndexBuilder()
builder.build_index(".")
response = builder.semantic_search(".", "Where do we create temporary login links?")

for result in response.results:
    print(result.chunk.file_path, result.chunk.symbol_name, result.similarity_score)
```

Incremental indexing example:

```python
from src.embeddings import EmbeddingIndexBuilder

builder = EmbeddingIndexBuilder()
builder.update_index(".")
builder.reindex_changed_files(".")
```

Embedding controls:

```
EMBEDDING_MAX_CHUNK_CHARS=2400
EMBEDDING_FALLBACK_LINES=80
EMBEDDING_FALLBACK_DIMENSION=384
EMBEDDING_FORCE_FALLBACK=false
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
RepositoryIndexer + DependencyMapper + RepositoryRetrievalEngine
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
RepositoryIndexer + DependencyMapper + RepositoryRetrievalEngine
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

Modification tasks use an explicit guarded workflow instead of naive file rewriting. The Slack route is preview-first: it generates a patch, produces a diff, validates safety, and waits for approval before any filesystem write.

```
modification request
  ↓
RepositoryRetrievalEngine + ContextSelector
  ↓
ModificationRequest
  ↓
PatchGenerator structured operations
  ↓
CodeModifier preview
  ↓
SafetyGuard + ChangeValidator
  ↓
DiffGenerator review output
  ↓
approval wait
```

Useful local examples:

```python
from src.modification import CodePatch, DiffGenerator, PatchChange

patch = CodePatch(
    summary="Update greeting.",
    changes=[
        PatchChange(
            file_path="example.py",
            old_content="def greet():\n    return 'hi'\n",
            new_content="def greet():\n    return 'hello'\n",
            modification_reason="Return the intended greeting.",
        )
    ],
)

print(DiffGenerator().generate_diff(patch))
```

```python
from src.modification import SafetyGuard

result = SafetyGuard().validate_modification(patch)
print(result.ok, result.approval_required)
```

```python
from src.modification import PatchApplier

applier = PatchApplier()
applied = applier.apply_patch(patch, ".", approved=True)
applier.rollback_patch(applied, ".")
```

Optional validation:

```
MODIFICATION_RUN_PYTEST=true
```

## Validation & Verification Engine

Generated patches are verified before they are treated as successful. The validation layer is modular and does not retry, fix, commit, push, or run autonomous loops:

```
CodePatch
  ↓
SyntaxValidator
  ↓
ImportChecker
  ↓
TestRunner
  ↓
LintRunner
  ↓
ValidationReporter
  ↓
ValidationReport
```

Patch validation runs tests and linting against a temporary repository overlay, so proposed code can be checked without modifying the real working tree.

Useful local examples:

```python
from src.modification import CodePatch, PatchChange
from src.validation import ValidationEngine

patch = CodePatch(
    summary="Update value.",
    changes=[
        PatchChange(
            file_path="example.py",
            old_content="value = 1\n",
            new_content="value = 2\n",
        )
    ],
)

report = ValidationEngine().validate_patch(patch, ".")
print(report.report_text)
```

```python
from src.validation import TestRunner

result = TestRunner(timeout_seconds=30).run_tests(
    ".",
    command=["python3", "-m", "unittest", "discover", "-s", "tests"],
)
print(result.summary)
```

```python
from src.validation import LintRunner

runner = LintRunner()
print(runner.detect_linters())
print(runner.run_linting(".").summary)
```

Validation controls:

```
VALIDATION_TEST_TIMEOUT_SECONDS=90
VALIDATION_LINT_TIMEOUT_SECONDS=60
VALIDATION_TEST_COMMAND=python3 -m unittest discover -s tests
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
