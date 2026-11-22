# Agentic_Chatbot

Let's go step by step. First, let me check your repo structure quickly based on your `CLAUDE.md` to give you accurate commands.

---

## Step 1 — Prepare Your Repo

Before touching Railway, make these small changes locally.

### 1a. Create a `requirements.txt` at root level (if not already there)
Your `CLAUDE.md` says `requirements.txt` is at root — good, keep it there.

### 1b. Update Redis connection to read from environment
Find where you connect to Redis in `src/utils/file_utils.py` and change it from:
```python
# Old - hardcoded
redis_client = redis.Redis(host='localhost', port=6379)
```
to:
```python
# New - reads from Railway's env variable
import os
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
redis_client = redis.from_url(redis_url)
```

### 1c. Create a `railway.toml` at repo root
```toml
[build]
builder = "nixpacks"

[deploy]
startCommand = "cd src && python app.py"
restartPolicyType = "on-failure"
```

### 1d. Make sure `transactions.db` is committed to GitHub
Your `CLAUDE.md` says it's already checked in (~880KB) ✅

### 1e. Make sure `.env` is in `.gitignore` ✅
Your `CLAUDE.md` confirms this — good, never push API keys.

---

## Step 2 — Push Code to GitHub

If not already on GitHub:
```bash
git add .
git commit -m "prepare for railway deployment"
git push origin main
```

---

## Step 3 — Sign Up on Railway

1. Go to [railway.app](https://railway.app)
2. Click **Start a New Project**
3. Sign up with **GitHub** (important — use GitHub so Railway can access your repo)
4. You'll get **$5 free trial credit** automatically

---

## Step 4 — Deploy FastAPI Service

1. After logging in → click **New Project**
2. Select **Deploy from GitHub repo**
3. Find and select your repo (`Chatbot_BE_Bank` or whatever it's named)
4. Railway will auto-detect Python and start building
5. Once deployed, go to **Settings** tab of the service
6. Scroll to **Networking** → click **Generate Domain**
7. You'll get a URL like `https://chatbot-be-bank-production.up.railway.app` — **copy this**

---

## Step 5 — Add Redis

1. In the same Railway project → click **+ New Service**
2. Select **Database → Add Redis**
3. Railway spins up Redis and **automatically** adds `REDIS_URL` as an env variable to your project
4. No config needed — it just works ✅

---

## Step 6 — Add Environment Variables

1. Click on your **FastAPI service** (not Redis)
2. Go to **Variables** tab
3. Add these one by one:

```
ANTHROPIC_API_KEY        = your_anthropic_key
OPENAI_API_KEY           = your_openai_key
DASHBOARD_USER_IDS       = your_value
DASHBOARD_PASSWORD       = your_value
LLM_PROVIDER             = anthropic
```

> `REDIS_URL` is already injected automatically by Railway from Step 5 — don't add it manually.

---

## Step 7 — Verify Deployment

1. Go to your FastAPI service → **Deployments** tab
2. Click the latest deployment → view **logs**
3. Look for something like:
```
INFO:     Started server process
INFO:     Uvicorn running on 0.0.0.0:8000
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