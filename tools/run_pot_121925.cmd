@echo off
REM #229 实时底池(pot_size,表级伪座0)。先用stack模板跨区读,--verify弹图核对+产pot真值。
REM 用法:  tools\run_pot_121925.cmd
python tools\digit_probe.py --field stack --read-field pot_size --harvest-file tools\truth_digit_121925.txt --session "data\recordings\20260603_121925" --validate 15 --verify --verify-out tools\verified_pot_121925.txt
