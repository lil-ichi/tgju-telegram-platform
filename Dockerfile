# ── Docker (optional) ──────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY tgju/ tgju/

# Runtime state dir (git-ignored locally, created here)
RUN mkdir -p /app/tgju/state

# The dashboard listens on 8791
EXPOSE 8791

# Bind 0.0.0.0 so the container is reachable from the host
CMD ["python", "tgju/tgju_platform.py"]
