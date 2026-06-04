@echo off
chcp 65001 >nul
REM #229 pot bigN: 3 recordings x30 frames. Templates harvested from DUAN1 stack
REM (--harvest-session fixed!), then read pot_size on each session. ASCII comments to avoid GBK mojibake.
echo ====== DUAN1 pot (same-recording) ======
python tools\digit_probe.py --field stack --read-field pot_size --harvest-file tools\truth_digit_121925.txt --harvest-session "data\recordings\20260603_121925" --session "data\recordings\20260603_121925" --validate 30 --verify --verify-out tools\verified_pot_121925_n30.txt
echo ====== DUAN2 pot (cross-recording) ======
python tools\digit_probe.py --field stack --read-field pot_size --harvest-file tools\truth_digit_121925.txt --harvest-session "data\recordings\20260603_121925" --session "data\recordings\20260603_130651" --validate 30 --verify --verify-out tools\verified_pot_130651_n30.txt
echo ====== 170343 pot (cross-recording) ======
python tools\digit_probe.py --field stack --read-field pot_size --harvest-file tools\truth_digit_121925.txt --harvest-session "data\recordings\20260603_121925" --session "data\recordings\20260602_170343" --validate 30 --verify --verify-out tools\verified_pot_170343_n30.txt
echo Done. push 3 files: tools\verified_pot_*_n30.txt
