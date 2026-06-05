@echo off
chcp 65001 >nul
REM 数字配方兜底读 stack:① 采模板 ② 秒验 seat2 全下"0"能否兜底读出。
REM 免长命令跨终端粘贴丢参数。用法(已激活 .venv):  tools\run_digit_fallback.cmd
echo === (1/2) 采 stack 模板(从 121925 的真值)→ rois\digit_templates_party_poker_8.json ===
python tools\build_digit_templates.py --session "data\recordings\20260603_121925" --harvest-file "tools\truth_digit_121925.txt" --out "rois\digit_templates_party_poker_8.json"
echo.
echo === (2/2) 秒验:用该模板读 170343 seat2 的全下 0(f_000400.png)===
python tools\probe_zero.py --session "data\recordings\20260602_170343" --frame f_000400.png --seat 2 --templates "rois\digit_templates_party_poker_8.json"
echo.
echo === 看上面 [C] 行:read 0 = 成立(进全量评估);read None = 模板没跨座迁过来(告诉我补左侧0)===
