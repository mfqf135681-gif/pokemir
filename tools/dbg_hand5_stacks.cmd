@echo off
REM Diagnose why hand-5 seat6 turn bet 74 vanished under fallback.
REM Dumps per-seat stack plateau trajectories over the hand-5 window (t186-262), A vs B.
REM Look at the seat6 line: in A a ~74 drop (turn bet) should appear; in B it is merged/lost.
REM Usage (activated .venv):  tools\dbg_hand5_stacks.cmd
echo === A  no fallback (pure EasyOCR): stack trajectories t186-262 ===
python tools\replay_reconstruct.py --session "data\recordings\20260602_170343" --start 186 --end 262 --decimate 5 --dump-stacks
echo.
echo === B  with digit-template fallback ===
python tools\replay_reconstruct.py --session "data\recordings\20260602_170343" --start 186 --end 262 --decimate 5 --dump-stacks --digit-templates "rois\digit_templates_party_poker_8.json"
echo.
echo === Compare the seat6 plateau line A vs B: where did the 74 drop go ===
