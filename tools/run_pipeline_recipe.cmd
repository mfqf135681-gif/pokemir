@echo off
REM Lever A live test: stack reading via the digit recipe (DigitReader) instead of EasyOCR.
REM Recipe primary (~10x faster), EasyOCR fallback only when recipe reads empty (all-in %).
REM Needs rois\digit_templates_party_poker_8.json (run_digit_fallback.cmd makes it).
REM
REM Compare phase log vs the baseline (run_pipeline_bench.cmd):
REM   seat_stack_ocr  should drop a lot (recipe ~10x);
REM   seat_fold_ocr   should drop some (EasyOCR stack batch skipped);
REM   tick median     should drop.
REM Accuracy: watch a few seats — stacks should track real chip counts (not all None/garbage).
REM
REM PREREQ: a REAL/stable WePoker table at 1454x1287. Run ~1-2 min, Ctrl-C. Stats -> postgres.
REM Usage (activated .venv):  tools\run_pipeline_recipe.cmd
set POKEMIR_USE_GPU=1
set POKEMIR_SHOWDOWN_DUMP=0
set POKEMIR_DIGIT_RECIPE_LIVE=1
python main.py pipeline --profile party_poker_8
