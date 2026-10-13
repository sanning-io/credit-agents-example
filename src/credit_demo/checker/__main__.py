"""`python -m credit_demo.checker`: serve the checker on its own settings' port."""

from __future__ import annotations

import uvicorn

from ..common.config import bind_host
from .config import load as load_settings
from .web import create_app


def main() -> int:
    settings = load_settings()
    uvicorn.run(create_app(settings), host=bind_host("checker"), port=settings.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
