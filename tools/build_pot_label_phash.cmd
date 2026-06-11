@echo off
REM Build "zongdichi" (total-pot label) text-shape phash ref from a clean recording frame.
REM Crop is saved under tools\output\pot_label (NOT repo root). Ref -> rois\pot_label_phash_party_poker_8.json
REM ASCII-only to avoid cmd.exe codepage mojibake. Usage (in activated .venv): tools\build_pot_label_phash.cmd
echo === Build pot-label phash: clean frame f_000232 (positive) + label session numbers (negatives) ===
python tools\build_pot_label_phash.py --from-frame "data\recordings\20260602_170343\frames\f_000232.png" "data\label_sessions\pot_size_20260610_184524"
echo.
echo === Check above: hash len = 64 (NOT 0); inter_min ^> intra_max (separable); match_threshold suggested ===
echo === Then OPEN tools\output\pot_label\frame_f_000232.png and confirm it shows the 3 chars cleanly ===
echo === If hash len = 0 (no white text grabbed) -> tell Claude, sat_th/val_th needs tuning ===
