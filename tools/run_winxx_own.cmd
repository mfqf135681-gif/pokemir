@echo off
chcp 65001 >nul
REM +xx OWN multi-exemplar templates from verified_winxx. "+" is LEFTMOST for ALL seats
REM ("+xxx" reads left-to-right) -> drop leading for all (NO icon-right-seats). Then --check.
python tools\digit_probe.py --field win_amount --harvest-file tools\verified_winxx_121925.txt --session "data\recordings\20260603_121925" --icon-prefix --check tools\verified_winxx_121925.txt
