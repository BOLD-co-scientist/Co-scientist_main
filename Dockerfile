FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

# Claude Agent SDK shells out to the `claude` CLI for transport. Install it.
RUN npm install -g @anthropic-ai/claude-code

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
RUN pip install -e ".[science,dev]"

# Source is bind-mounted at runtime; this COPY only seeds the image so it builds.
COPY . /app

ENV PYTHONPATH=/app
EXPOSE 8765

CMD ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8765"]
