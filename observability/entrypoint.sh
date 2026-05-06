#!/bin/sh
set -e

# Install python3 and pip (alpine/git image)
apk add --no-cache python3 py3-pip

# Clone/pull the latest version of the script
REPO_URL="https://github.com/duplocloud/opentelemetry-release.git"

if [ -d "opentelemetry-release" ]; then
    cd opentelemetry-release
    git pull
else
    git clone $REPO_URL opentelemetry-release
    cd opentelemetry-release
fi

cd observability

# Install requirements
pip3 install --break-system-packages -r requirements.txt

# Run the script
python3 otel-observability.py 