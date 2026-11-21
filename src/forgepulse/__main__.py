from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("FORGEPULSE_HOST", "127.0.0.1")
    port = int(os.environ.get("FORGEPULSE_PORT", "8080"))
    uvicorn.run("forgepulse.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
