# FinTell — Banking Transactions Chatbot

FastAPI + LangGraph backend with a single-page chat UI. Users ask natural-language questions about
their transactions (answered via text-to-SQL over SQLite) or about
Barclays help-centre topics (answered via RAG over a Chroma store).

## Run locally

```powershell
pip install -r requirements.txt
copy .env.example .env      # then add ANTHROPIC_API_KEY and OPENAI_API_KEY (embeddings)
python src/app.py           # http://localhost:8000
```

Redis is optional. See [AGENTS.md](AGENTS.md) for commands, conventions and pitfalls, and
[docs/architecture.md](docs/architecture.md) for diagrams and the agent, endpoint and data-layout reference.
