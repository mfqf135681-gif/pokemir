@echo off
REM Stage 0 (a)(c): run the live pipeline to capture real per-tick phase breakdown.
REM GPU on = reproduce the real production bottleneck. Phase stats print every ~20 ticks
REM AND emit to postgres diagnostic_events (tag=pipeline.tick_stats) for Claude to read.
REM
REM BEST: have a WePoker table visible on screen (live or a table screenshot at 1454x1287)
REM so OCR hits real content. No table = phase STRUCTURE still valid, OCR ms understated.
REM
REM Let it run ~1-2 min (watch a few "[tick stats]" lines), then press Ctrl-C to stop.
REM Usage (activated .venv):  tools\run_pipeline_bench.cmd
set POKEMIR_USE_GPU=1
python main.py pipeline --profile party_poker_8
