#!/bin/bash
set -e

# Install git if not present (depending on base image)
if ! command -v git &> /dev/null; then
    apt-get update && apt-get install -y git
fi

# Clone/pull the latest version of the script
REPO_URL="https://github.com/duplocloud/opentelemetry-release.git"
SCRIPT_PATH="otel-observability.py"

if [ -d "opentelemetry-release" ]; then
    cd opentelemetry-release
    git pull
else
    git clone $REPO_URL opentelemetry-release
    cd opentelemetry-release
fi

cd observability
# Install requirements
pip install -r requirements.txt

# SCRIPT_NAME selects which collection script to run.
# Defaults to otel-observability.py (main-stack behaviour unchanged).
# Set SCRIPT_NAME=collector-observability.py for collector-only deployments.
SCRIPT_NAME=${SCRIPT_NAME:-otel-observability.py}
python $SCRIPT_NAME