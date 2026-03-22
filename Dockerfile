FROM python:3.11-slim

WORKDIR /app

# System deps (gcc needed by some Python packages)
RUN apt-get update && apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

# Install Python deps first (cached layer — only re-runs when requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Source code is bind-mounted at runtime (see docker-compose.yml)
# This keeps the image small and lets you update code with git pull + docker compose restart
