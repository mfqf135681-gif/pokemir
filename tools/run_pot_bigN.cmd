@echo off
REM #229 底池厚样本:三录像各30帧(段1同录像 + 段2/170343跨录像),stack模板跨读pot_size。
REM ~90样本,够支撑统计论断 + 跨录像。--verify弹图,~100%的ROI大多回车很快。
REM 用法:  tools\run_pot_bigN.cmd   (跑完push 3个verified_pot_*_n30.txt)
echo ====== 段1 pot (同录像) ======
python tools\digit_probe.py --field stack --read-field pot_size --harvest-file tools\truth_digit_121925.txt --session "data\recordings\20260603_121925" --validate 30 --verify --verify-out tools\verified_pot_121925_n30.txt
echo ====== 段2 pot (跨录像) ======
python tools\digit_probe.py --field stack --read-field pot_size --harvest-file tools\truth_digit_121925.txt --session "data\recordings\20260603_130651" --validate 30 --verify --verify-out tools\verified_pot_130651_n30.txt
echo ====== 170343 pot (跨录像) ======
python tools\digit_probe.py --field stack --read-field pot_size --harvest-file tools\truth_digit_121925.txt --session "data\recordings\20260602_170343" --validate 30 --verify --verify-out tools\verified_pot_170343_n30.txt
echo 全部跑完。push 3个 tools\verified_pot_*_n30.txt 给我。
