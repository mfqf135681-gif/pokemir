@echo off
REM Levers A + D both on: recipe stack reading (A) + one-grab full-frame capture (D).
REM Compare phase log vs the A-only run (run_pipeline_recipe.cmd):
REM   capture_frame  = the single full-window grab cost (NEW; should be ~one grab);
REM   seat_actions   = should DROP a lot (the ~37 per-ROI grabs are gone, sliced from cache);
REM   community      = should drop too (board ROIs now sliced, not grabbed);
REM   tick median    = should drop.
REM
REM FIRST verify D equivalence (slice == grab, byte-level) on a STATIC screen:
REM   python capture\screen.py     -> expect "D.1 等价" OK
REM
REM PREREQ: real/stable WePoker table at 1454x1287. Run ~1-2 min, Ctrl-C. Stats -> postgres.
REM Usage (activated .venv):  tools\run_pipeline_levers.cmd
set POKEMIR_USE_GPU=1
set POKEMIR_SHOWDOWN_DUMP=0
set POKEMIR_DIGIT_RECIPE_LIVE=1
set POKEMIR_FRAME_CAPTURE=1
python main.py pipeline --profile party_poker_8
