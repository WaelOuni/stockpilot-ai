#!/usr/bin/env bash

# Activate virtualenv
if [ -d ".venv" ]; then
    source .venv/bin/activate
else
    echo ".venv not found"
    echo "Create it with: python3 -m venv .venv"
fi

export WINDOWS_HOST="$(ip route | awk '/default/ {print $3}')"
export OLLAMA_BASE_URL="http://${WINDOWS_HOST}:11434"
export OLLAMA_MODEL="qwen2.5:7b"
export OLLAMA_EMBEDDING_MODEL="nomic-embed-text"

echo "WINDOWS_HOST=${WINDOWS_HOST}"
echo "OLLAMA_BASE_URL=${OLLAMA_BASE_URL}"
echo "OLLAMA_MODEL=${OLLAMA_MODEL}"
