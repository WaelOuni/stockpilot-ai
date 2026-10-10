#!/usr/bin/env bash

# Load local environment variables
if [ -f ".env.local" ]; then
    set -a
    source .env.local
    set +a
fi

# Activate virtualenv
if [ -d ".venv" ]; then
    source .venv/bin/activate
else
    echo ".venv not found"
    echo "Create it with: python3 -m venv .venv"
fi

# Detect Windows host from WSL
export WINDOWS_HOST="$(ip route | awk '/default/ {print $3; exit}')"

# Dynamic Ollama configuration
export OLLAMA_BASE_URL="http://${WINDOWS_HOST}:11434"

# Defaults, but keep values from .env.local if already defined
export OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:7b}"
export OLLAMA_EMBEDDING_MODEL="${OLLAMA_EMBEDDING_MODEL:-nomic-embed-text}"

echo "WINDOWS_HOST=${WINDOWS_HOST}"
echo "OLLAMA_BASE_URL=${OLLAMA_BASE_URL}"
echo "OLLAMA_MODEL=${OLLAMA_MODEL}"
echo "OLLAMA_EMBEDDING_MODEL=${OLLAMA_EMBEDDING_MODEL}"