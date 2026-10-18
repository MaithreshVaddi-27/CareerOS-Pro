FROM python:3.12-slim

WORKDIR /app

# No build toolchain. Every pinned dependency ships a cp312 manylinux wheel, so
# build-essential was ~500 MB of dead weight that nothing used. If a future
# dependency needs to compile from source, add it back deliberately rather than
# carrying it for a dependency set that does not need it.

# Install as root, before dropping privileges. Installing while already switched
# to careeros put every package in ~/.local and left the console scripts
# (uvicorn, pytest) off PATH — it worked only because the CMD happens to invoke
# `python -m backend.run`. Anything that shells out to `uvicorn` would have
# failed at runtime in the container and nowhere else.
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd -m careeros

# Application code only; .dockerignore keeps backend/venv (585 MB of macOS
# wheels, wrong platform for the image) out of the build context entirely.
COPY --chown=careeros:careeros backend/ ./backend/

USER careeros

# Expose backend port
EXPOSE 8000

# Container-level health check — docker/orchestrator can now see when the app
# isn't ready (e.g. DB unreachable) instead of treating "container running"
# as "container healthy". No curl in this slim image, so use Python directly.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3).status == 200 else 1)"]

# Start the backend with Uvicorn
CMD ["python", "-m", "backend.run", "--host", "0.0.0.0", "--port", "8000"]
