FROM python:3.11-slim

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    COMFYUI_URL=http://host.docker.internal:8188 \
    OUTPUT_DIR=/app/output \
    DATA_DIR=/app/data \
    HOST=0.0.0.0 \
    PORT=8080

WORKDIR /app

# Install system dependencies if required (e.g. for image processing)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app/ /app/app/

# Ensure output and data mount points exist
RUN mkdir -p /app/output /app/data /app/data/thumbnails

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/api/status || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
