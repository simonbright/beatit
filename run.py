#!/usr/bin/env python3
import os
from pathlib import Path

import uvicorn

from app.config import settings


def main():
    # Default off: file-watcher reload was restarting/killing the server when
    # SQLite/uploads under data/ changed. Set BEATIT_RELOAD=1 while coding.
    reload = os.getenv("BEATIT_RELOAD", "0").strip().lower() in {"1", "true", "yes"}
    kwargs: dict = {
        "app": "app.main:app",
        "host": settings.host,
        "port": settings.port,
        "reload": reload,
    }
    if reload:
        app_dir = Path(__file__).resolve().parent / "app"
        kwargs["reload_dirs"] = [str(app_dir)]
    uvicorn.run(**kwargs)


if __name__ == "__main__":
    main()
