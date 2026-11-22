# FinTell — Banking Transactions Chatbot

FastAPI + LangGraph backend with a single-page chat UI. Users ask natural-language questions about
their transactions (answered via text-to-SQL over SQLite, with optional Plotly charts) or about
Barclays help-centre topics (answered via RAG over a Chroma store).

## Run locally

```powershell
pip install -r requirements.txt
copy .env.example .env      # then add ANTHROPIC_API_KEY and OPENAI_API_KEY (embeddings)
python src/app.py           # http://localhost:8000
```
4. Open your Railway domain URL in browser
5. You should see your chat UI (`index.html`) ✅

---

## Step 8 — How to Pause & Resume Between Demos

**To pause** (after demo):
- Railway dashboard → your FastAPI service → **Settings** → scroll down → **Pause Service**
- Do the same for Redis service

**To resume** (30 min before demo):
- Same place → **Resume Service**
- Wait ~30 seconds for boot
- Open the URL, send a test message to warm it up

---

## Step 9 — Share With Client

Just share the Railway domain URL from Step 4:
```
https://your-app-name.up.railway.app
```
That's it — the client opens it and sees the chat UI directly. No Streamlit needed since FastAPI already serves `index.html` at `GET /`.

## Run locally with Qwen3-8B

The app can use the assigned OpenAI-compatible Qwen endpoint for chat. Copy `.env.example` to `.env` and set:

```dotenv
LLM_PROVIDER=qwen
QWEN_API_KEY=your_rotated_qwen_api_key
QWEN_API_BASE_URL=https://103.42.50.41:1031/v1
QWEN_MODEL=Qwen/Qwen3-8B
QWEN_CA_CERT=C:\Users\yourname\Downloads\vm07-ca.crt
```

Download the CA certificate assigned to your group and set `QWEN_CA_CERT` to its actual path. The app uses that certificate for both synchronous and asynchronous HTTPS requests. Keep `.env` local; it is ignored by Git. An `OPENAI_API_KEY` is not required for Qwen chat, but OpenAI-dependent embeddings/help-centre retrieval and voice transcription remain unavailable without it.

Install dependencies and start the backend from the repository root:

```powershell
pip install -r requirements.txt
python src/app.py
```

---

## Quick Checklist Before First Demo

- [ ] Redis connection updated in `file_utils.py`
- [ ] `railway.toml` added to repo root
- [ ] Code pushed to GitHub
- [ ] FastAPI service deployed and domain generated
- [ ] Redis service added
- [ ] All env variables set (Anthropic key, OpenAI key, etc.)
- [ ] Tested the URL yourself before showing client

---

If you hit any errors during deployment, share the Railway build logs here and I'll help you debug them.