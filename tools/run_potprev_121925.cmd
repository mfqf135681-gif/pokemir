@echo off
REM #229 上街底池(pot_size_previous,表级伪座0,粗小)。stack模板跨读 + verify核对。
REM 用法:  tools\run_potprev_121925.cmd
python tools\digit_probe.py --field stack --read-field pot_size_previous --harvest-file tools\truth_digit_121925.txt --session "data\recordings\20260603_121925" --validate 15 --verify --verify-out tools\verified_potprev_121925.txt
