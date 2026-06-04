@echo off
chcp 65001 >nul
REM diagnose +xx bad seats (s0/s1/s5): --check verified truth under 3 settings, see which recovers them.
REM zero re-verify. read win_amount with DUAN1 stack templates.
set BASE=--field stack --read-field win_amount --harvest-file tools\truth_digit_121925.txt --session "data\recordings\20260603_121925" --check tools\verified_winxx_121925.txt
echo ============ A: default (normalize on, ink-th 150) ============
python tools\digit_probe.py %BASE%
echo ============ B: --no-normalize ============
python tools\digit_probe.py %BASE% --no-normalize
echo ============ C: --ink-th 100 ============
python tools\digit_probe.py %BASE% --ink-th 100
echo ============ D: --no-normalize --ink-th 100 ============
python tools\digit_probe.py %BASE% --no-normalize --ink-th 100
