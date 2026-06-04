@echo off
chcp 65001 >nul
REM Test percentile-normalize (p2-p98): did +xx improve? did pot regress? non-interactive --check.
set TPL=--field stack --harvest-file tools\truth_digit_121925.txt --harvest-session "data\recordings\20260603_121925"
echo ====== +xx (win_amount) recheck (was 20/32) ======
python tools\digit_probe.py %TPL% --read-field win_amount --session "data\recordings\20260603_121925" --check tools\verified_winxx_121925.txt
echo ====== pot regression guard DUAN1 (was 23/24) ======
python tools\digit_probe.py %TPL% --read-field pot_size --session "data\recordings\20260603_121925" --check tools\verified_pot_121925_n30.txt
echo ====== pot regression guard DUAN2 cross-rec (was 28/28) ======
python tools\digit_probe.py %TPL% --read-field pot_size --session "data\recordings\20260603_130651" --check tools\verified_pot_130651_n30.txt
echo ====== amount regression guard DUAN2 ======
python tools\digit_probe.py %TPL% --read-field amount --icon-prefix --icon-right-seats "5,6,7" --session "data\recordings\20260603_130651" --check tools\verified_amount_130651.txt
