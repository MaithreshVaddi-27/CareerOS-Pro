FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m careeros
USER careeros

# Copy requirements first for caching
COPY --chown=careeros:careeros requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code (no .env files — mount secrets at runtime)
COPY --chown=careeros:careeros backend/ ./backend/

# Expose backend port
EXPOSE 8000

# Container-level health check — docker/orchestrator can now see when the app
# isn't ready (e.g. DB unreachable) instead of treating "container running"
# as "container healthy". No curl in this slim image, so use Python directly.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3).status == 200 else 1)"]

# Start the backend with Uvicorn
CMD ["python", "-m", "backend.run", "--host", "0.0.0.0", "--port", "8000"]
