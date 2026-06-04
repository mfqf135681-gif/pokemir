@echo off
chcp 65001 >nul
REM +xx HELD-OUT: 段1 +xx-own templates read 段2's +xx (cross-recording, genuinely new wins).
REM "+" leftmost all seats -> drop leading. scan finds showdown wins; --verify pops crop + confirm.
python tools\digit_probe.py --field win_amount --harvest-file tools\verified_winxx_121925.txt --harvest-session "data\recordings\20260603_121925" --session "data\recordings\20260603_130651" --icon-prefix --scan 5 --min-hit-cells 3 --verify --verify-out tools\verified_winxx_130651.txt
