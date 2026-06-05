# 切手过切 bug + 多信号交叉印证修复(T139)

## §1 任务概述
回放评估时发现"34 手"实为 ~13 真手——**切手严重过切**,把整套 per-hand 评估
(守恒 47%/21%、recall、"0 动作大池")建在碎片上。修:切手改多信号交叉印证。
- 触发:用户手工 --truth 标注(虽痛)+ 实测 "D 按钮 187-261 一直在 s3 = 1 手"。
- 关联:[[constraint-solver-paradigm]] §15 评估地基 · T132(旧并集切手)。

## §2 发现经过(怎么揪出来的)
1. 手工标 hand 12 测 recall,数怎么都对不上;
2. 用户看视频:**D 钮从 187 到 261 全程在 s3 → 按建筑学这是 1 手**(钮每手只移一次),
   机器却切成 3 手(我当时叫的 12/13/14);
3. `--dump-signals` 坐实:**按钮干净**(s2→s3→s4),**公共牌有假 reset@232**(某帧误读 0 张);
4. 根因:`--conservation`(无 --truth)走 `build_series_real` **不读按钮** → 回退公共牌 reset
   切手,假 reset@232 把 1 手劈成 3。全量:**34 碎片 vs 13 真手**(过切 ~2.6×)。

## §3 实现(两步)
1. **守恒路径读按钮**(7e83388):`build_series_real` 加 button_rois → 返回 button_series,
   else 分支算 hand_starts(与 --truth 路径一致)。
2. **多信号交叉印证 + 按钮硬约束**(42afaef,T139):
   - `button_moves_monotonic`:去抖(持有<min_hold=闪读丢)+ **顺时针单调**(新座=(旧+k)%
     num_seats,k∈[1,4];倒退/远跳=误读拒)。party_poker_8 座 index 已顺时针(0底→1/2/3左→4顶→5/6/7右)。
   - `corroborate_boundaries`:候选按时间聚类,**含按钮 anchor OR ≥2 不同信号** 才算真边界
     → 否决孤立假信号;**无按钮降级宽松**(单信号=旧并集)防欠切。
   - `segment_hands` 重写:汇集 button/community/payout/ante/win_ends 候选 → 交叉印证。

## §4 影响(13 真手 vs 34 碎片)
| 指标 | derived(碎) | btn(按钮·真) |
|---|---|---|
| 手数 | 34 | **13** |
| 守恒 OK | 47%(空) | **61%(真)** |
| "0 动作大池" | 12 手 | **0**(全是碎片) |
| 平均动作/手 | 1.1 | **4.4** |
- "0 动作大池"谜底揭穿=真手被切走翻前段后剩的中后段残片。

## §5 验证(Linux 静态/逻辑)
- `pipeline/reconstruct.py` 全自测过(含按钮切手 4 手窗 / win-分段);
- 新增交叉印证单测:232 假 reset 被否决(有按钮)/ 闪读 s5 被滤 / 无按钮降级宽松;
- replay --mock + 语法 + 3.14 help。**按钮白占比读 cv2 Win-only 未验(盲点)**;Win 快验 187-261 仍 1 手。

## §6 红线 / 破坏性
无破坏性(纯逻辑改 replay/reconstruct,不碰 orchestrator/Path A=封存合规、不碰 DB schema)。

## §7 教训(沉淀)
- **conservation 是必要不充分**:守恒"空 OK"(0 进 0 出)把切碎的残片掩盖了,自动指标没报警;
- **单信号切手脆**:任一信号误判即过切/欠切;**多信号交叉印证才稳**(圈梁思路用在切手);
- **手工标注虽痛,揪出了 auto-metric 藏的 bug** —— 没白标;
- **附:负 cm 误诊**:曾把负 cm 当"派彩污染末态"提议截断 → 反而把守恒手判 CHECK,自测反证、撤回未 ship
  (守恒口径末态【必须取派彩后】,cm≈rake)。负 cm 真因(输家下注没进末态/补码/边界)未查,留 dump stack 诊断。

## §8 后续
- 负 cm 诊断:dump 一手(如 hand 11)per-seat init/final 平台 → 定因再修;
- recall 真测:用按钮真边界(如 187-262 整手)重标重跑;
- win-phash 候选已留 `config["win_ends"]` 接口,plumbing 进 conservation 路径可再加一路印证。
