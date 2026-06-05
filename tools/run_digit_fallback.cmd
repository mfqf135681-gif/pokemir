@echo off
REM Build stack digit templates, then verify the all-in "0" can be read via fallback.
REM ASCII-only to avoid cmd.exe codepage mojibake. Usage (in activated .venv):  tools\run_digit_fallback.cmd
echo === (1/2) Harvest stack templates from 121925 truth -^> rois\digit_templates_party_poker_8.json ===
python tools\build_digit_templates.py --session "data\recordings\20260603_121925" --harvest-file "tools\truth_digit_121925.txt" --out "rois\digit_templates_party_poker_8.json"
echo.
echo === (2/2) Verify: read 170343 seat2 all-in 0 (f_000400.png) with the templates ===
python tools\probe_zero.py --session "data\recordings\20260602_170343" --frame f_000400.png --seat 2 --templates "rois\digit_templates_party_poker_8.json"
echo.
echo === Check [C] line: read 0 = OK (go run A/B eval); read None = templates did not transfer ===
