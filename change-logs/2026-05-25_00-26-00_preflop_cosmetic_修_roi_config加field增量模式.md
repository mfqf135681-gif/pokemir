# preflop cosmetic 修 + `roi_config --field` 增量模式

- **完成时间**：2026-05-25 00:26
- **关联需求讨论**：`requirement-discussions/2026-05-24_20-54-00_PathA第3步_Win端pipeline全链路smoke.md`（path A 第 3 步迭代,本次为收口前小修）
- **关联前次 change-log**：`change-logs/2026-05-25_00-17-00_pipeline空槽过滤_community_reset_handstart.md`
- **触发红线**：无
- **无关红线已检查**：R-1 到 R-10 全无触发

## 1. 任务概述

Path A 第 3 步 smoke 已通过(双手 hand 循环正常),但有 2 个小瑕疵 + 1 个长期 UX 坑:

| # | 问题 | 修法 |
|:---:|:---|:---|
| 1 | `Street preflop: ['6s']` / `['4d', 'Kd']` —— 显示 1-2 张牌的非规范 street（flop 翻牌动画过程的中间帧）| detector 仅在 community count ∈ {0, 3, 4, 5} 时上报 street 切换 |
| 2 | `pot_size` ROI 仍 null → pipeline 不识别底池 | 用户需 Win 端配 pot ROI |
| 3 | `roi_config.py` "完全覆写" footgun —— 跑一次就抹掉其它 ROI | 加 `--field` 增量模式,只配一个字段保留其它 |

修法 #3 顺带解决用户配 pot ROI 问题(不需要重配所有,只需 `--field pot_size`)。

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | 1-2 张 community 是翻牌动画中间帧,跳过即可 | 用户实测:flop 翻牌 0→1→2→3 过程,1/2 是中间态 |
| 2 | 内部 `_prev_community_count` 仍需准确更新(用于 community_just_reset 检测)| `_prev_community_count = count` 总是执行;只 return 值受限 |
| 3 | --field 加 community_1-5 映射到 community_cards[0-4],其它字段(hero/pot)直接 set | 命名约定 community_1 = 第 1 张(1-indexed),array index = N-1 |

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `pipeline/detector.py` | 修改（+15/-3 行）| `check_community_change` 引入 `_CANONICAL_COUNTS = {0,3,4,5}`;count 不在规范集时**仍更新内部状态**(prev count + just_reset flag)**但 return False** + 不调 normalizer.set_community_card_count;避免误标 street |
| `tools/roi_config.py` | 修改（+45/-2 行）| (a) 加 `VALID_FIELDS` 集合;(b) 加 `--field` CLI 选项(choices 限定);(c) 增加增量模式分支:`--field` 设时,加载现有 JSON → 只 prompt 该字段 → 用 select_roi 取新坐标 → merge 回 JSON → 写入;特殊处理 `community_N`(N=1-5)映射到 `community_cards[N-1]` |

### 附带修复（5 分钟规则）

无。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更：**否**
- ROI JSON schema 不变;`--field` 只是更省心地修改字段
- StateTracker 公共 API 不变

## 5. 红线合规动作

无触发。

## 6. 测试结果

- **smoke 1: canonical 过滤**:
  ```
  count=1 → logs=False ✓
  count=2 → logs=False ✓
  count=3 → logs=True ✓ (flop)
  count=4 → logs=True ✓ (turn)
  count=5 → logs=True ✓ (river)
  count=0 → logs=True + just_reset=True ✓ (new hand)
  ```
- **smoke 2: --help 显示增量字段选项** ✓
- **pytest sanity**: 14 passed / 3 skipped / 0 failed ✓

## 7. 手动操作提醒

⚠️ **Win 用户配 pot ROI 的新流程**:

```powershell
# 增量配 pot_size,不动其它 ROI
.\.venv\Scripts\python.exe tools\roi_config.py --name party_poker --field pot_size

# 验证
.\.venv\Scripts\python.exe tools\roi_config.py --verify --name party_poker

# 立刻 commit + push（这次别忘）
git add rois\party_poker.json
git commit -m "加 pot_size ROI"
git push
```

之后再跑 pipeline,应该:
- preflop 不再显示 1-2 张牌噪声
- pot 在 OCR 读到底池数字后会 log `latest_pot_bb = X`(虽然 log 在 orchestrator 里没显式打印,但内部 tracker 已记录)

## 8. 潜在影响范围

- **正向**：
  - 日志清晰: 只在真实 street(preflop/flop/turn/river)时上报
  - `--field` 增量模式可复用：未来想动单个 ROI 不再重配全套
- **行为变化**：
  - 真实 game 同样受益（不会显示动画中间帧)
- **关联待办**：
  - 用户用 `--field pot_size` 配 pot
  - 用户**记得 commit 这次的 rois/party_poker.json**(之前 5 community 没 commit 的教训)
  - seat ROIs 仍是 path A 第 3 步外scope

## 9. 违规标注

无。
