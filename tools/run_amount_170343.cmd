@echo off
REM A路:段1+段2 合厚pool,在170343(留出)上交互核对amount。免长命令跨终端粘贴。
REM 用法(在已激活.venv的PowerShell/cmd里):  tools\run_amount_170343.cmd
python tools\digit_probe.py --field amount --session "data\recordings\20260602_170343" --harvest-src "data\recordings\20260603_121925|tools\truth_amount_121925.txt" --harvest-src "data\recordings\20260603_130651|tools\verified_amount_130651.txt" --icon-prefix --icon-right-seats "5,6,7" --scan 5 --min-hit-cells 3 --verify --verify-out tools\verified_amount_170343.txt
