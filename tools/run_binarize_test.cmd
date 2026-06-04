@echo off
chcp 65001 >/dev/null
set STK=--field stack --harvest-file tools\truth_digit_121925.txt --harvest-session "data\recordings\20260603_121925"
echo ====== +xx 段2 all-seats --binarize (baseline 26/29; s0 was the 3 errors) ======
python tools\digit_probe.py --field win_amount --harvest-file tools\verified_winxx_121925.txt --harvest-session "data\recordings\20260603_121925" --session "data\recordings\20260603_130651" --icon-prefix --binarize --check tools\verified_winxx_130651.txt
echo ====== pot 段1 regression --binarize (baseline 23/24) ======
python tools\digit_probe.py %STK% --read-field pot_size --session "data\recordings\20260603_121925" --binarize --check tools\verified_pot_121925_n30.txt
echo ====== amount 段2 regression --binarize ======
python tools\digit_probe.py %STK% --read-field amount --icon-prefix --icon-right-seats "5,6,7" --session "data\recordings\20260603_130651" --binarize --check tools\verified_amount_130651.txt
