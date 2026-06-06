@echo off
REM Stage 0 (b): OCR throughput + recipe-vs-EasyOCR on numeric ROIs.
REM Reports A/B/C (EasyOCR all-ROI variants) + A_num/D (EasyOCR vs DigitReader on stack/amount/pot).
REM ASCII-only. Usage (activated .venv):  tools\run_bench_tick.cmd
REM Uses rois\digit_templates_party_poker_8.json if present (run_digit_fallback makes it).
set POKEMIR_USE_GPU=1
python tools\bench_ocr.py --frame "data\recordings\20260602_170343\frames\f_000100.png" --profile party_poker_8 --iters 20
echo.
echo === Key line: D 配方 Hz and (xN vs A_num) = recipe speedup on the numeric ROIs ===
echo === NOTE: this measures PROCESSING only. Capture cost (lever D, grab) needs a live-screen probe. ===
