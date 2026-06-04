@echo off
chcp 65001 >nul
REM +xx OWN multi-exemplar templates from verified_winxx (yellow win glyphs incl s0/s1/s5's own),
REM then --check same file. Circular (train=test) but confirms: do own-templates fix s0/s1/s5?
REM icon-prefix drops leading/trailing "+"; try right-side = 4,5,6,7 per user's 0-3/4-7 split.
python tools\digit_probe.py --field win_amount --harvest-file tools\verified_winxx_121925.txt --session "data\recordings\20260603_121925" --icon-prefix --icon-right-seats "4,5,6,7" --check tools\verified_winxx_121925.txt
