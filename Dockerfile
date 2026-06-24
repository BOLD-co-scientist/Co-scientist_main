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

RUN npm install -g @anthropic-ai/claude-code

# --- LAB REQUIREMENT: Add non-root user ---
ARG UID
ARG GID
RUN groupadd -o -g ${GID:-1000} myuser && \
    useradd -u ${UID:-1000} -g ${GID:-1000} -m myuser

WORKDIR /app

# Ensure the new user owns the working directory
RUN chown -R myuser:myuser /app

# Switch to the new non-root user
USER myuser

# Add the local user bin to PATH so pip-installed binaries (like uvicorn) are recognized
ENV PATH="/home/myuser/.local/bin:${PATH}"

# --- APP SETUP ---
# Copy files and ensure ownership belongs to 'myuser'
COPY --chown=myuser:myuser pyproject.toml /app/pyproject.toml
COPY --chown=myuser:myuser . /app

# Install Python dependencies as the non-root user. Keep the [dev] extras so
# pytest ships in the image — the strict-path evolution smoke gate runs it.
RUN pip install -e ".[science,dev]"

ENV PYTHONPATH=/app
EXPOSE 8765

CMD ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8765"]