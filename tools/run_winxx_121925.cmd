@echo off
chcp 65001 >nul
REM #229 +xx (win_amount, per-seat, showdown-only, leading "+"). Read with DUAN1 stack templates.
REM Read-only test: "+" drops as '?' naturally (like chip icon), no icon flags needed.
REM scan finds showdown win displays; --verify pops crop + confirm + produces truth.
echo ====== +xx (win_amount) read test, DUAN1 ======
python tools\digit_probe.py --field stack --read-field win_amount --harvest-file tools\truth_digit_121925.txt --session "data\recordings\20260603_121925" --scan 5 --min-hit-cells 2 --verify --verify-out tools\verified_winxx_121925.txt
echo Done. push tools\verified_winxx_121925.txt
