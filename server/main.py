"""CLI entrypoint: python -m server.main"""

from __future__ import annotations

import os


def main() -> None:
    import uvicorn
    from .app import create_optimized_app

    host = os.getenv("TTS_HOST", "0.0.0.0")
    port = int(os.getenv("TTS_PORT", "7788"))
    app = create_optimized_app()
    print(f"GPU TTS server: http://{host}:{port}")
    print(f"  health: http://{host}:{port}/health")
    uvicorn.run(app, host=host, port=port, log_level=os.getenv("TTS_LOG_LEVEL", "info").lower(), workers=1)


if __name__ == "__main__":
    main()
