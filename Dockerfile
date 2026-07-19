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
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        texlive-latex-recommended \
        texlive-latex-extra \
        texlive-fonts-recommended \
        latexmk \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

# --- LAB REQUIREMENT: Add non-root user ---
ARG UID
ARG GID
RUN groupadd -o -g ${GID:-1000} myuser && \
    useradd -u ${UID:-1000} -g ${GID:-1000} -m myuser

WORKDIR /app

# --- APP SETUP ---
# Copy the source in first (as root) so the editable install can resolve it.
COPY pyproject.toml /app/pyproject.toml
COPY . /app

# Install Python dependencies into the SYSTEM site-packages (/usr/local), as
# root, BEFORE dropping to the non-root user. This is CRITICAL and non-obvious:
# MCP tools run in contexts that do NOT see the per-user site
# (/home/myuser/.local). py_exec spawns the system python with HOME overridden,
# and the research runtime executes tools in a uid-remapped sandbox that never
# mounts the user site — so a user-site install makes pandas/numpy/rapidocr/etc.
# silently unimportable at tool-call time (verified live). The system site is on
# sys.path unconditionally and is readable in both contexts, so the deps must
# live there. Build-time root is fine; the RUNNING container is still non-root
# (USER myuser below) per BOLD/FLAIR Rule 1. Keep [dev] so pytest ships for the
# strict-path evolution smoke gate.
RUN pip install --root-user-action=ignore -e ".[science,dev,ocr]"

# Hand the working tree to the non-root user and switch to it for runtime.
RUN chown -R myuser:myuser /app
USER myuser

ENV PYTHONPATH=/app
EXPOSE 8765

CMD ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8765"]