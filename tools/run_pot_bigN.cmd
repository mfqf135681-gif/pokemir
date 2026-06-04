@echo off
chcp 65001 >nul
REM #229 pot cross-recording test. Templates from DUAN1 stack (--harvest-session), read pot on DUAN2 / 170343.
REM DUAN1 itself already validated 23/24, no re-run. ASCII comments to avoid cmd GBK mojibake.
echo ====== DUAN2 pot (cross-recording, x30) ======
python tools\digit_probe.py --field stack --read-field pot_size --harvest-file tools\truth_digit_121925.txt --harvest-session "data\recordings\20260603_121925" --session "data\recordings\20260603_130651" --validate 30 --verify --verify-out tools\verified_pot_130651_n30.txt
echo ====== 170343 pot (cross-recording, x30) ======
python tools\digit_probe.py --field stack --read-field pot_size --harvest-file tools\truth_digit_121925.txt --harvest-session "data\recordings\20260603_121925" --session "data\recordings\20260602_170343" --validate 30 --verify --verify-out tools\verified_pot_170343_n30.txt
echo Done. push tools\verified_pot_130651_n30.txt tools\verified_pot_170343_n30.txt
