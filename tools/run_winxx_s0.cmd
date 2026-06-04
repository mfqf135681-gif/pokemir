@echo off
chcp 65001 >/dev/null
REM Re-test ONLY s0 +xx on 段2 after re-framing s0 win_amount to dodge table texture.
REM Templates re-harvested from 段1 with NEW s0 ROI; read 段2 s0 only.
python tools\digit_probe.py --field win_amount --harvest-file tools\verified_winxx_121925.txt --harvest-session "data\recordings\20260603_121925" --session "data\recordings\20260603_130651" --icon-prefix --only-seats 0 --scan 5 --min-hit-cells 3 --verify --verify-out tools\verified_winxx_s0_130651.txt
