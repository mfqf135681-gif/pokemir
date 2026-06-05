@echo off
chcp 65001 >nul
REM 13手 --truth 评估 A/B:先无兜底(基线)、再加数字配方兜底 → 当场对比 precision。
REM 同命令同窗(--start 0 --end 9999 --decimate 5 --p6),唯一差别=有无 --digit-templates。
REM 用法(已激活 .venv,先 git pull + 跑过 run_digit_fallback 生成模板):  tools\run_truth_eval_ab.cmd
echo === A 基线(无兜底,纯 EasyOCR)===
python tools\replay_reconstruct.py --session "data\recordings\20260602_170343" --start 0 --end 9999 --decimate 5 --truth "tools\truth_170343_full.txt" --p6
echo.
echo === B 加数字配方兜底(EasyOCR读空→配方补读,治全下0)===
python tools\replay_reconstruct.py --session "data\recordings\20260602_170343" --start 0 --end 9999 --decimate 5 --truth "tools\truth_170343_full.txt" --p6 --digit-templates "rois\digit_templates_party_poker_8.json"
echo.
echo === 对比两段的 recall / precision:重点看 h2/h3/h10 全下街错 FP 是否在 B 段消失 ===
