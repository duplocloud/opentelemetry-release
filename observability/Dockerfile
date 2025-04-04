# Use Python 3.9 slim image as base
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the script
COPY otel-observability.py .

# Set environment variables
ENV PROMETHEUS_URL=""
ENV LOKI_URL=""
ENV CLUSTER=""
ENV NAMESPACE=""
ENV CUSTOMER=""
ENV ENVIRONMENT=""
ENV DUPLO_URL=""

# Run the script
CMD ["python", "otel-observability.py"] 