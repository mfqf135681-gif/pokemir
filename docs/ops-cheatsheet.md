# Pokemir 运维 / 诊断命令速查

> 目的:把 live 运行、ROI 诊断/重框、帧录制、动作 phash 重建、DB 查询等**常用命令**集中一处,
> 带详细注释,免去每次翻源码。命令以 **Win 端**为主(pipeline/截屏只能在 Win 跑)。
> 维护:命令的 flag 以工具 `--help` / 源码 argparse 为准;改了工具记得同步本文件。
> 最后更新:2026-06-20(s0 动作丢失排障会话固化)。

---

## 0. 关键事实(排障前先记住)

| 项 | 值 | 说明 |
|---|---|---|
| ROI profile | `party_poker_8` | 8 座桌型;`--profile party_poker_8` / 环境 `POKEMIR_ROI_PROFILE` |
| 锁定分辨率 | `1454 x 1287` | profile `resolution` 字段 = **card_marker 金丝雀**;live 窗口尺寸必须等于它,否则全框集体平移 |
| seat_0 | = hero(你自己) | 底部居中,UI 与对手座**不同尺寸**;摊牌/hero检测特殊处理 |
| `action` ≈ `id` 区域 | 同一块像素 | 动作汉字与昵称同位置 → 此框漂移会同时害**动作丢失 + 昵称读空** |
| DB | Win 本地 PG18 `100.77.23.17:5432` / `poker_assistant` / `poker_user` | Claude MCP 只读;写删走 psycopg2 |
| 帧库路径规则 | `data/recordings/<YYYYMMDD_HHMMSS>/frames/f_NNNNNN.png` + `manifest.jsonl` | `record_frames.py` 自动生成 |

**红线提醒**:覆盖 `rois/*.json` 或 `rois/action_refs_*.json` 前**必先备份**(见 §8)。

---

## 1. Live pipeline 运行

```bash
# 普通跑(到点干净退出,落库)
python main.py pipeline --profile party_poker_8 --max-minutes 8

# 观战模式(没坐下;关闭 hero 座检测,所有座走对手摊牌捕获)
python main.py pipeline --profile party_poker_8 --observer

# 入口:main.py;子命令只有 api(默认)/ pipeline。--max-minutes 与 Ctrl+C 同退出路径。
```

### 1.1 干净动作流(人话播报,不刷屏)
```bash
# POKEMIR_TABLE_QUIET=1 把主日志压到 WARNING,只剩 [牌桌] 人话动作流(独立 logger 不受压)
# bash:
POKEMIR_TABLE_QUIET=1 python main.py pipeline --profile party_poker_8 --max-minutes 8
# PowerShell:
$env:POKEMIR_TABLE_QUIET=1; python main.py pipeline --profile party_poker_8 --max-minutes 8

# 输出:控制台 + ./table_feed.log(带时间戳)。每手有 "════ 新一手 按钮=seat_X ════" 分隔。
# 看 seat_0 出不出真动作(跟注/加注/下注/过牌);只出 弃牌/盲注 = 动作丢失。
```

### 1.2 动作发射探针(零设置,定位"动作框 phash 匹配不上")
```bash
# [act seat_X] 只在 action_phash 匹配出动作词时才打印(orchestrator ~2690)。
# ⚠️ 必须普通 INFO,别加 TABLE_QUIET(它会把 INFO 的 [act] 一起压掉)。
python main.py pipeline --profile party_poker_8 --max-minutes 8 2>&1 | grep --line-buffered -F "[act seat_"

# 判读:别座一直刷、就是没 [act seat_0] → s0 动作框 phash 从不匹配(动作丢失咽喉)。
#       [act seat_0] text='让牌' -> ... → 框对了但参考词库缺该字形,需补锚(§5)。
```

### 1.3 日志文件
```bash
# 滚动日志(按天),gitignored:
#   logs/pokemir_YYYY-MM-DD.log
# 只看 s0 活跃集/假弃牌/hero 检测:
grep -E "hero-seat|活跃集|P2a fold救援|act seat_0|_detect_empty" logs/pokemir_$(date +%F).log
```

### 1.4 常用环境变量
| 变量 | 默认 | 作用 |
|---|---|---|
| `POKEMIR_LOG_LEVEL` | INFO | 日志级别 |
| `POKEMIR_TABLE_QUIET` | 0 | =1 压主日志,只留人话动作流(会隐藏 INFO 级 [act]) |
| `POKEMIR_ROI_PROFILE` | party_poker_9 | 用哪套 ROI(本项目用 party_poker_8) |
| `POKEMIR_USE_GPU` | 0 | EasyOCR GPU(Win 5070Ti 需 cu128 wheel) |
| `POKEMIR_OCR_BATCH` / `BATCH_SEAT_OCR` | 0 / 1 | 批 OCR 提速 |
| `POKEMIR_BUTTON_CUT` | — | 按钮检测切手(白占比 argmax) |
| `POKEMIR_AUDIT_DSN` | — | audit_session.py 的只读连接串 |

---

## 2. card_marker 诊断(活跃集金丝雀)

`tools/marker_hamming.py` — card_marker(头像左下两张红牌背)的 phash hamming 验证。

```bash
# ① 抓一帧 live → 整窗画各座 card_marker 框(绿=匹配/红=不匹配,标 hamming)+ 各座放大 crop
python tools/marker_hamming.py --dump --profile party_poker_8
#   输出 out/marker_check/_overlay.png + s<座>_ham<值>.png
#   判读:框压在两红牌背上但红=ref内容坏(重录 ref);框飘到头像/缝隙=框位坏(重框)。

# ② 实时逐座 hamming(连续抓多帧)
python tools/marker_hamming.py --live --profile party_poker_8 --samples 20 --interval 0.7 --th 8

# ③ 对一张已存帧算 hamming(对得上=进程跑旧框/分辨率漂;对不上=框或ref真错)
python tools/marker_hamming.py --from-image "data/recordings/<ts>/frames/f_000123.png" --profile party_poker_8

# ④ 单座 live 重录 card_marker_ref(只改该座,别座好 ref 不动;跑时该座必须露两张牌背!)
python tools/marker_hamming.py --set-ref 0 --profile party_poker_8
```

---

## 3. ROI 框:可视化 + 重框

### 3.1 把框画在帧上看对不对(draw_roi)
```bash
# 在一张帧上画指定 region 的框(8 座一起画,标 sX)。region 逗号分隔。
python tools/draw_roi.py --frame "out/marker_check/_overlay.png" --profile party_poker_8 --regions action,stack,amount
#   输出 tools/output/draw_roi/<frame>_annot.png
#   可用 region:action / stack / amount / fold_area / id / hand_type / win_amount / showdown_corner / cards / card_marker
```

### 3.2 交互式重框(roi_config,cv2 selectROI)
```bash
# ⚠️ 不带 --field 会重做整套 8 座 → 危险!只重单座单框务必用 --field + --element。
# 只重 seat_0 的 action 框(其它座/框保留):
python tools/roi_config.py --name party_poker_8 --seats 8 --field seat_0 --element action --window WePoker

# 在已存帧上重框(免对着 live 抢时机):
python tools/roi_config.py --name party_poker_8 --seats 8 --field seat_0 --element action \
    --frame "data/recordings/<ts>/frames/f_000123.png"

# 叠帧预览现有 profile(核框位):
python tools/roi_config.py --name party_poker_8 --seats 8 --verify
```
**合法 `--element` 值**(SEAT_ELEMENT_ORDER):
`action, amount, fold_area, fold_text, timer, stack, button_indicator, cards, id, hand_type, win_amount, card_marker, showdown_corner`
(必填项:`action`、`stack`)

---

## 4. 帧录制(record_frames)

```bash
# 默认 dxcam。窗口在副屏/负坐标会报 "Invalid Region" → 必须 --backend mss。
python tools/record_frames.py --window-title WePoker --backend mss --fps 5 --duration 900 --note "用途备注"

# 显式区域(绝对屏幕坐标 L T W H):
python tools/record_frames.py --region 100 80 1280 720 --backend mss --fps 5

# 产出:data/recordings/<YYYYMMDD_HHMMSS>/frames/f_NNNNNN.png(6位补零)+ manifest.jsonl
# 开录前交互问 sb/bb/ante(写进 manifest,回放自动用);随时 Ctrl+C,已落盘帧不丢。
```
- `--fps` 目标帧率;`--duration` 最长秒数;`--max-frames` 帧数硬上限(默认 12000)。
- **PII**:录整窗含他人昵称/聊天 → 本地自用、标完即删;敏感时 `--region` 排除聊天面板。

---

## 5. 动作 phash 参考 重建(action_refs)

**背景**:真实动作(call/check/raise/bet)的 live 发射被 **action 框 phash 闸**门控(`orchestrator.py:2663` `if not action_text: continue`),不是纯 stack 驱动。all_in 例外(走 stack→0 独立触发)。
参考文件 `rois/action_refs_party_poker_8.json` 按**词**存:`过牌/跟注/下注/加注`,每词多锚(8 座 × 4 词 = 32 锚)。
**重框 action 区 → 旧锚作废 → 必须重采锚**。标签:`check/raise/call/bet/fold`(LABEL_TO_WORD:过牌/加注/跟注/下注/弃牌)。

```bash
# 必须先有录像帧(§4)。先备份:
cp rois/action_refs_party_poker_8.json rois/action_refs_party_poker_8.json.bak

# ① 自动每座采集(推荐):每动作给 1 个种子锚(任意清楚的座),自动把各座 crop 聚类重采
python tools/auto_collect_action_refs.py \
    --frames-dir "data/recordings/<ts>/frames" --profile party_poker_8 \
    --seed-anchors "f_000097:3:check,f_000210:5:raise,f_000150:6:call,f_000320:2:bet" --dump
#   审 tools/output/action_refs_review/ 各座各动作放大图;错的 --exclude "3:call,5:bet" 重跑剔除。
#   ⚠️ 若某座 check 桌面字形不同(如 s0 显示"让牌"≠"过牌"),种子直接挑该座自己的帧(f_xxxx:0:check)。

# ② 手标锚精确建(只补特定座/词,--append 不覆盖已采的好座)
python tools/build_action_refs.py \
    --frames-dir "data/recordings/<ts>/frames" --profile party_poker_8 \
    --anchors "f_000097:0:check,f_001028:0:raise,f_001028:0:call,f_002175:0:bet" --threshold 10 --append

# ③ 验收探针:扫全录像,看每动作参考 hamming 是否 bimodal(命中低簇/idle 高簇)
python tools/probe_action_phash.py \
    --frames-dir "data/recordings/<ts>/frames" --profile party_poker_8 \
    --anchors "f_000097:0:check,f_000210:3:raise" --thresholds 6,8,10,12 --dump
```
重建后回 §1.2 用 `[act seat_X]` 探针验 live 是否拾取到动作。

---

## 6. 离线回放重建(replay_reconstruct)

```bash
# 录制帧 → stack 轨迹 → reconstruct → 对真值量捕获率(无 fps 压力,验 §15 架构正确性)
python tools/replay_reconstruct.py \
    --session "data/recordings/<ts>" --profile party_poker_8 \
    --start 120 --end 195 --truth hand1_truth.txt --sb 2 --bb 4 --ante 4 --pot 470
#   --start/--end = 该手时间窗(秒,从标注的"第几分钟"换算)。Linux 仅 --mock 自测核。
```

---

## 7. Session 审计(audit_session,只读)

```bash
# 逐手守恒核算 + 端点法赢家归属;分类 EXACT/GAP_POS(漏抓)/GAP_NEG(超读)/UNKNOWN
POKEMIR_AUDIT_DSN="postgresql://poker_user:<pwd>@100.77.23.17:5432/poker_assistant" \
    python tools/audit_session.py --since 2026-06-20T00:00:00Z --tol 2 --dump out/audit.jsonl
#   报守恒率前必先确认切手对(手数 vs 按钮移座数);conservation 必要不充分。
```

---

## 8. 常用 DB 查询(SQL — Claude 走 MCP 只读,或 psql)

```sql
-- 8.1 按本地日期看有哪些 session
SELECT (started_at AT TIME ZONE 'Asia/Shanghai')::date AS d, count(*) AS hands,
       min(started_at AT TIME ZONE 'Asia/Shanghai') AS first,
       max(started_at AT TIME ZONE 'Asia/Shanghai') AS last
FROM hands WHERE started_at > now() - interval '7 days'
GROUP BY d ORDER BY d DESC;

-- 8.2 某天各座【真实动作】vs【合成弃牌救援】分布(seat_index 在 raw_data;真实动作 synthetic IS NULL)
SELECT (a.raw_data->>'seat_index') AS seat,
       count(*) FILTER (WHERE a.action_type IN ('call','check','raise','bet','all_in') AND a.raw_data->>'synthetic' IS NULL) AS real_acts,
       count(*) FILTER (WHERE a.action_type='fold' AND a.raw_data->>'source'='activeset_fold_rescue') AS rescue_fold,
       count(*) AS total
FROM action_events a JOIN hands h ON h.id=a.hand_id
WHERE (h.started_at AT TIME ZONE 'Asia/Shanghai')::date = DATE '2026-06-20'
GROUP BY 1 ORDER BY (a.raw_data->>'seat_index')::int;
--  某座 real_acts=0 而别座正常 = 该座动作被整条丢(本次 s0 病象)。

-- 8.3 "动作全丢"签名扫描:某座被 rescue-fold 且零真实动作的手数
WITH s AS (
  SELECT h.id, (h.started_at AT TIME ZONE 'Asia/Shanghai')::date AS d,
    count(*) FILTER (WHERE a.action_type='fold' AND a.raw_data->>'source'='activeset_fold_rescue') AS rf,
    count(*) FILTER (WHERE a.action_type IN ('call','check','raise','bet','all_in') AND a.raw_data->>'synthetic' IS NULL) AS real
  FROM hands h JOIN action_events a ON a.hand_id=h.id AND (a.raw_data->>'seat_index')='0'
  WHERE (h.started_at AT TIME ZONE 'Asia/Shanghai')::date = DATE '2026-06-20'
  GROUP BY h.id, d)
SELECT d, count(*) AS hands, count(*) FILTER (WHERE rf>0 AND real=0) AS lost_signature,
       count(*) FILTER (WHERE real>0) AS has_real FROM s GROUP BY d;

-- 8.4 最近一手的全部动作行(看赢家 + 是否丢动作)
SELECT a.sequence_number AS seq, a.street, (a.raw_data->>'seat_index') AS seat,
       a.player_name, a.action_type, a.amount, a.raw_data->>'source' AS source
FROM hands h JOIN action_events a ON a.hand_id=h.id
WHERE h.id=(SELECT id FROM hands ORDER BY started_at DESC LIMIT 1)
ORDER BY a.sequence_number;
--  hands.result jsonb: win_seats_xx / winners_endpoint / win_amounts_xx / sources_agree

-- 8.5 诊断事件全景(最近 3 小时各 tag 计数 + 最新 payload)
SELECT tag, level, count(*) AS n, (array_agg(payload ORDER BY occurred_at DESC))[1] AS latest
FROM diagnostic_events WHERE occurred_at > now() - interval '3 hours'
GROUP BY tag, level ORDER BY n DESC LIMIT 30;
--  关注:showdown.hero_seat_detected(observer/null=hero检测失败)、fold.activeset_rescue、
--        all_in.detected、player.tempuser_assigned(action_text_contamination)。
```

---

## 9. 备份(覆盖前必做,红线)

```bash
# 重框 / 重采锚 前:
cp rois/party_poker_8.json            rois/party_poker_8.json.bak
cp rois/action_refs_party_poker_8.json rois/action_refs_party_poker_8.json.bak
# cmd: copy rois\party_poker_8.json rois\party_poker_8.json.bak
# 备份只放项目内,绝不用桌面(OneDrive 重定向风险)。
```

---

## 10. Worked example:"seat_0 动作全丢"完整排障链(2026-06-20)

1. **现象**:某座(s0/hero)整场零真实动作,别座正常 → §8.2 / §8.3 锁定。
2. **赢家却无动作** = 铁证(赢家不可能弃)→ §8.4 看 `winners_endpoint:[0]` 但 s0 真实动作=0。
3. **排 card_marker**:§2 `--dump` 看 s0 框压牌背 + hamming(ham0=完好,排除活跃集假弃)。
4. **排框位**:§3.1 draw_roi 画 action/stack/amount;§3.2 重框漂移的 action 区。
5. **查机制**:动作发射被 action phash 闸门控(§5 背景);§1.2 `[act seat_0]` 探针确认 phash 不匹配。
6. **根因**:UI 微调 → action 区漂移 → 旧锚失效。窗口仍 1454x1287(分辨率没漂)。
7. **修**:§4 录新帧 → §5 重采锚 → §1.2 验 live → §8.2 验 DB。
8. **教训**:① 验"识别对不对"要走**完整 pipeline + 动作流/DB**,别只信单点工具(hamming 工具给过假绿灯);
   ② 重框 ROI **必须重采对应 phash 锚**(锚不动 = 必失败)。
```
