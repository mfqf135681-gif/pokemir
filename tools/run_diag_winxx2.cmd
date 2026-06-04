@echo off
chcp 65001 >/dev/null
REM diagnose +xx bad vs good seats: column-ink profile + gray min/max (detect white-bg-black-text inversion).
python tools\digit_probe.py --field stack --read-field win_amount --harvest-file tools\truth_digit_121925.txt --session "data\recordings\20260603_121925" --diagnose --check tools\diag_winxx_pick.txt
