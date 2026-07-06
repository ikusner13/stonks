"""Export the FastAPI OpenAPI schema."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.web.app import app


def main() -> None:
    payload = json.dumps(app.openapi(), indent=2)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(payload + "\n")
    else:
        print(payload)


if __name__ == "__main__":
    main()
