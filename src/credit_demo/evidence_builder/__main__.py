"""`python -m credit_demo.evidence_builder`: serve the evidence builder on its
own settings' port."""

from __future__ import annotations

from ..common.listen import serve
from .config import load as load_settings
from .web import create_app


def main() -> int:
    settings = load_settings()
    serve(create_app(settings), settings.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
