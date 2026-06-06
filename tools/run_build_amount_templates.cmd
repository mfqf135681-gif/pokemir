@echo off
REM Build AMOUNT-specific digit templates (persist to rois\) so live orchestrator stops
REM reading amount with STACK templates (the cut1 3<->4 bug). Sources + icon-right-seats
REM "5,6,7" reuse the verified #229 amount truth (no re-labeling). Held-out validate on 170343.
REM ASCII-only to avoid cmd.exe codepage mojibake. Usage (in activated .venv):
REM   tools\run_build_amount_templates.cmd
echo === Build amount templates from 121925 + 130651, validate held-out on 170343 ===
python tools\build_digit_templates.py --field amount --icon-prefix --icon-right-seats "5,6,7" --session "data\recordings\20260603_121925" --harvest-file "tools\truth_amount_121925.txt" --harvest-session "data\recordings\20260603_121925" --harvest-file "tools\verified_amount_130651.txt" --harvest-session "data\recordings\20260603_130651" --validate-file "tools\verified_amount_170343.txt" --validate-session "data\recordings\20260602_170343" --out "rois\digit_templates_party_poker_8_amount.json"
echo.
echo === Read the "liu-chu validate ... = NN%%" line: that is the true held-out accuracy vs 95%% ===
echo === Output file rois\digit_templates_party_poker_8_amount.json is auto-loaded by live ===
