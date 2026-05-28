"""Poker Learning Assistant — main entry point.

Usage:
    python main.py                              # Start the API server
    python main.py pipeline                     # Run capture pipeline (default profile)
    python main.py pipeline --profile NAME      # Run with explicit ROI profile
"""

import argparse


def main():
    parser = argparse.ArgumentParser(description="Poker Learning Assistant")
    parser.add_argument(
        "command",
        nargs="?",
        default="api",
        choices=["api", "pipeline"],
        help="api (default): start API server. pipeline: run capture loop.",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="ROI profile name (e.g. party_poker_9, party_poker_8). "
             "Defaults to POKEMIR_ROI_PROFILE env var or config.py default. "
             "Used in pipeline mode; ignored in api mode.",
    )
    parser.add_argument(
        "--observer",
        action="store_true",
        help="观战模式 — 用户未坐下。关闭 hero seat 自动检测,所有 seat 走对手摊牌捕获。"
             "Used in pipeline mode; ignored in api mode.",
    )
    args = parser.parse_args()

    if args.command == "pipeline":
        from pipeline.orchestrator import PipelineOrchestrator
        orchestrator = PipelineOrchestrator(roi_profile=args.profile, observer_mode=args.observer)
        orchestrator.start()
    else:
        import uvicorn
        from api.server import create_app
        from config import API_HOST, API_PORT
        app = create_app()
        uvicorn.run(app, host=API_HOST, port=API_PORT)


if __name__ == "__main__":
    main()
