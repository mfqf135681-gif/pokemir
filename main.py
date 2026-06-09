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
        # ⚠️⚠️⚠️ 临时压测块(显存占用测试)— 用完即删,勿进生产!grep "VRAM压测" 定位删除整段 ⚠️⚠️⚠️
        # POKEMIR_VRAM_FILL_GB=N → GPU 占住 N GB 显存(静态持有 _vram_hog,主程序退出自动释放),
        # 测牌CNN/录制在显存压力下扛不扛得住。删除范围 = 本块(到下面"临时压测块 结束")。
        import os as _os
        _vram_gb = float(_os.getenv("POKEMIR_VRAM_FILL_GB", "0"))
        _vram_hog = None
        if _vram_gb > 0:
            import torch as _torch

            def _smi():
                import subprocess
                try:
                    return subprocess.check_output(
                        ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu,temperature.gpu",
                         "--format=csv,noheader,nounits"], text=True).strip().replace("\n", " | ")
                except Exception as _e:
                    return f"(nvidia-smi 读不到: {_e})"

            if not _torch.cuda.is_available():
                print("[VRAM压测] 无 CUDA,跳过占显存")
            else:
                print(f"[VRAM压测] 占前 nvidia-smi(used,total,util%,temp MB/%/C): {_smi()}")
                try:
                    _vram_hog = _torch.empty(int(_vram_gb * 1024 ** 3), dtype=_torch.uint8, device="cuda")
                    _torch.cuda.synchronize()
                    print(f"[VRAM压测] ✅ 已占 {_vram_gb} GB(torch_allocated={_torch.cuda.memory_allocated()/1024**3:.2f} GB)")
                    print(f"[VRAM压测] 占后 nvidia-smi: {_smi()}")
                except RuntimeError as _e:
                    print(f"[VRAM压测] ❌ 分配 {_vram_gb} GB 失败(OOM/余量不足): {_e}")
                    print(f"[VRAM压测] 现状 nvidia-smi: {_smi()}")
        # ⚠️⚠️⚠️ 临时压测块 结束 ⚠️⚠️⚠️
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
