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
REM
REM CLEAN-MEASURE config (2026-06-05): shadow_pointer now OFF by default (code);
REM SHOWDOWN_DUMP off here = no per-hand disk writes in the transition path.
REM For a TRUE steady-state, point at a REAL/stable WePoker table (not a static/blank screen,
REM which makes _start_new_hand misfire and inflates hand_detect).
set POKEMIR_USE_GPU=1
set POKEMIR_SHOWDOWN_DUMP=0
python main.py pipeline --profile party_poker_8
