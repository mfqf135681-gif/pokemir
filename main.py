"""Poker Learning Assistant — main entry point.

Usage:
    python main.py                  # Start the API server
    python main.py pipeline         # Run the capture → recognize → store pipeline
"""

import sys


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "pipeline":
        from pipeline.orchestrator import PipelineOrchestrator
        orchestrator = PipelineOrchestrator()
        orchestrator.start()
    else:
        import uvicorn
        from api.server import create_app
        from config import API_HOST, API_PORT
        app = create_app()
        uvicorn.run(app, host=API_HOST, port=API_PORT)


if __name__ == "__main__":
    main()
