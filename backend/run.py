"""Single entrypoint. `python run.py` starts the API — no docker, no bare server
command to remember. Runs on Uvicorn (ASGI server) for broad compatibility.
"""

import argparse
import sys
from pathlib import Path

import uvicorn

# Load environment variables from .env file in project root
# This ensures config can find MYSQL_PASSWORD, API_KEY, etc. regardless of where we run from
project_root = Path(__file__).parent.parent
env_file = project_root / ".env"
if env_file.exists():
    from dotenv import load_dotenv
    load_dotenv(env_file)

# Add the project root to Python path so backend can be imported as a module
# This works whether we run from project root or backend directory
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from backend.core.config import get_settings  # noqa: E402 — must follow the sys.path fix-up above


def main():
    get_settings()  # fail fast — surface a bad/missing env var here, before uvicorn boots

    parser = argparse.ArgumentParser(description="Run the CareerOS API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", default=False,
                         help="auto-reload on code change (off by default)")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    uvicorn.run(
        "backend.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers,
        log_level="info",
    )


if __name__ == "__main__":
    main()