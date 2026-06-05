@echo off
REM 13-hand --truth eval A/B: baseline (no fallback) vs digit-template fallback.
REM Same window (--start 0 --end 9999 --decimate 5 --p6); only diff = --digit-templates.
REM ASCII-only to avoid mojibake. Usage (activated .venv, after run_digit_fallback):  tools\run_truth_eval_ab.cmd

REM --- guard: truth file must exist; restore from git if the Win working tree lost it ---
if not exist "tools\truth_170343_full.txt" (
  echo [warn] tools\truth_170343_full.txt missing -- restoring from git HEAD...
  git checkout -- tools\truth_170343_full.txt
)
if not exist "tools\truth_170343_full.txt" (
  echo [error] still missing. Run:  git pull   then re-run this script.
  exit /b 1
)

echo === A  baseline (no fallback, pure EasyOCR) ===
python tools\replay_reconstruct.py --session "data\recordings\20260602_170343" --start 0 --end 9999 --decimate 5 --truth "tools\truth_170343_full.txt" --p6
echo.
echo === B  with digit-template fallback (EasyOCR empty -^> recipe reads, fixes all-in 0) ===
python tools\replay_reconstruct.py --session "data\recordings\20260602_170343" --start 0 --end 9999 --decimate 5 --truth "tools\truth_170343_full.txt" --p6 --digit-templates "rois\digit_templates_party_poker_8.json"
echo.
echo === Compare recall/precision; key: does h2/h3/h10 all-in street FP disappear in B ===
