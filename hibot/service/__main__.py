"""python -m hibot.service [--host 127.0.0.1] [--port 8077] [--workers 2] [--timeout 240] [--loop v1|v2]"""
from __future__ import annotations

import argparse
import logging
import os
import sys


def main(argv=None) -> int:
    from hibot.envfile import load_env
    load_env()                                       # HIBOT_SERVICE_KEY, DB and LLM settings from the git-ignored .env
    p = argparse.ArgumentParser(prog="hibot.service", description="HiBot HTTP service")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8077)
    p.add_argument("--workers", type=int, default=2, help="concurrent analyses")
    p.add_argument("--timeout", type=float, default=240.0, help="per-run timeout in seconds")
    p.add_argument("--loop", choices=["v1", "v2"], default="v1")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not os.environ.get("HIBOT_SERVICE_KEY"):
        print("error: HIBOT_SERVICE_KEY is not set (put it in the git-ignored .env); refusing to start", file=sys.stderr)
        return 2
    try:
        import uvicorn
        from .app import create_app
    except ImportError as e:
        print("error: %s. Install the optional service dependencies: pip install fastapi uvicorn" % e, file=sys.stderr)
        return 2
    app = create_app(workers=a.workers, timeout=a.timeout, loop=a.loop)
    uvicorn.run(app, host=a.host, port=a.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
