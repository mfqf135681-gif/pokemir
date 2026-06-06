@echo off
REM Capture the EMPTY-table baseline for all B-relevant ROIs (timer/fold_text/fold_area/stack/amount),
REM all seats. Feeds lever B1 (skip-empty-OCR gate) + read-empty-frequency measurement.
REM
REM PREREQ: open WePoker at an EMPTY table (no players, no overlays) at the profile resolution
REM         (1454x1287). A wrong state/resolution = useless baseline.
REM ASCII-only. Usage (activated .venv):  tools\run_capture_empty_baseline.cmd
REM
REM Later, on a REAL table, capture state references (accumulate into the same file):
REM   python tools\capture_roi_reference.py --profile party_poker_8 --state fold  --seats 3
REM   python tools\capture_roi_reference.py --profile party_poker_8 --state allin --seats 5
python tools\capture_roi_reference.py --profile party_poker_8 --state empty
