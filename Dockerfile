# FinTell API + chat UI for Railway (or any Docker host).
# Railway injects $PORT; secrets come from service variables, never from the image.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# App code, prompts, the demo database and the committed help index (see .dockerignore).
COPY . .

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app:app --app-dir src --host 0.0.0.0 --port ${PORT:-8000}"]
