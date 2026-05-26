# Backlog 全做: OCR 鲁棒性 + P3 规则推断 + P4 review + 头像指纹

- **完成时间**：2026-05-25 20:41
- **关联 REQ**：`requirement-discussions/2026-05-25_19-11-00_交叉验证架构_4层金字塔_path_B衔接.md`(confirmed)
- **关联记忆**:[[cross-validation-architecture-pending]] → 本次大幅推进,后续将更新为 mostly-implemented
- **关联前次 change-log**：`change-logs/2026-05-25_20-05-00_A_player_name_filter_C_P2_layer1_confidence.md`
- **触发红线**：**R-7（pipeline 逻辑 + ROI 配置 + tools)**
- **无关红线已检查**：R-1 到 R-6, R-8, R-9, R-10

## 1. 任务概述

用户决议 "全做" — 把之前讨论决定但暂缓的 7 项 backlog 一次性实施。分 4 个 commit:

| Bundle | commit | 内容 | 行 |
|:---:|:---|:---|:---:|
| 1 | `30ed57e` | OCR 鲁棒性(#1 action allowlist + #2 ID lock + #3 fuzzy + #7 ALL IN) | 53 |
| 2 | `c07e190` | P3 Layer 2/3 规则推断 + stack-derived override | 89 |
| 3 | `ad78997` | P4 replay_review.py 离线 review CLI | 136 |
| 4 | `cf13c22` | 头像图像指纹(#4 avatar phash 玩家身份) | 58 |

**总计 ~336 行代码,4 commits,1 session**。

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | difflib.get_close_matches 默认算法对中文短字符串相似度计算合理 | smoke 测验证 |
| 2 | 8x8 average hash 对头像变化(玩家轻微动作 / 弃牌灰度)鲁棒,hamming 阈值 6 适合 | 业界 phash 经验 |
| 3 | fold_area 框作 avatar 源足够(头像中心覆盖)— 不增 ROI 字段 | 用户当前配置已含 fold_area |
| 4 | P3 stack_derived 是更可信信号 — REQ Q4=A 已 confirm | REQ 19-11 |
| 5 | replay_corrections 表 schema 已就位(P1 阶段确认) | contracts/models.sql |

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `pipeline/orchestrator.py` | 修改(+78/-15 行)| ACTION_OCR_ALLOWLIST 常量;_avg_hash_64 + _hamming helper;_process_seat_actions 用 allowlist + P3 infer+override;_capture_player_ids 加 hash lookup + fuzzy match + cache lock |
| `pipeline/detector.py` | 修改(+8/-1 行)| StateTracker 加 _street_to_call / _street_has_bet / _avatar_fingerprints;reset 与 street transition 联动 |
| `events/normalizer.py` | 修改(+45/-1 行)| 新增 infer_action_from_delta(stack_delta, to_call, is_first_bet, full_stack) 函数 |
| `tools/replay_review.py` | 新增(136 行)| 独立 CLI:psycopg2 连 PG,拉低 confidence events,y/n/a/m/q 交互,写 replay_corrections |

### 附带修复（5 分钟规则）

无(刻意忍住 — 已完成 4 bundle 已是 scope 上限)。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更：**否**
- DB schema 不变;`replay_corrections` 表早就有,本次首次写入

## 5. 红线合规动作

**R-7（pipeline + 工具一致性)** 触发:
- [x] ACTION_OCR_ALLOWLIST 字符集涵盖所有 parser 关键字(中英文)
- [x] avatar fingerprint hamming 阈值 6 是经验值,保守可调
- [x] P3 override 不静默修改:raw_data 完整记录 text_derived / stack_derived / override_reason
- [x] confidence ≤ 0.5 标记 override 事件,进 review 队列
- [x] replay_corrections 写入只追加,不修改原 action_events,可逆且审计完整

## 6. 测试结果

- **fuzzy match smoke**:
  ```
  疯鱼罩轩 → 疯鱼覃轩 ✓ (4-char 1 差,ratio 0.75)
  系统的爹是 → 系统的蜚是 ✓
  DGMT199 → []        ✓ (不误匹配 DGMT168)
  林道九 → []          ✓ (3-char 1 差 ratio 0.67 < 0.75)
  ```
- **infer_action_from_delta smoke**:7 个典型 case 全 ✓(check/fold/call/bet/raise/all_in)
- **avg_hash smoke**:same img → hamming 0;diff img → hamming 36(远超阈值 6)
- **replay_review.py --help**:argparse 展示正常,3 个 flag(threshold/limit)
- **pytest**:14 passed / 3 skipped / 0 failed(各 bundle 后回归一致)

## 7. 手动操作提醒

⚠️ **Win 端用户(下次 pipeline 运行验证)**:

### A. `git pull` 拉 4 个新 commit

```powershell
cd D:\project\pokemir
git pull
.\.venv\Scripts\python.exe main.py pipeline --profile party_poker_8
```

### B. 跑 30+ 手观察改变

预期看到的新行为:
- **OCR 噪声大幅减少**:action_text 不再出现 "疯鱼罩轩 2" 类污染
- **同玩家不再多变体**:疯鱼覃轩/罩轩 自动合并;系统的蜚是/爹是 合并
- **player_id_map 跨手 lock**:ID OCR 失败的 seat 在下手不会被改名
- **P3 override 日志**:console 偶现 `[P3 override] seat_X text=...` — 表明 stack-derived 救了 text-derived 误判
- **confidence 分布改善**:0.3 占比应大幅下降(P3 用规则推断替代失败的 text 后,大多数能拉回 1.0)

### C. P4 review CLI 使用(可选,VPS 上跑)

```bash
cd /home/alxe/project/pokemir
.venv/bin/python tools/replay_review.py --threshold 0.7 --limit 20
```

交互按键:
- `y` (默认) 接受 — 不修
- `n` / `s` skip
- `a` 改 action_type(提示输入新值)
- `m` 改 amount
- `q` 退出

写入 `replay_corrections` 表,不动 action_events 原行。Path B 统计时 SQL `LEFT JOIN replay_corrections` 用最新修正。

## 8. 潜在影响范围

- **正向**:
  - 整条数据流多重防御 — 每层 catch 不同类问题
  - 玩家身份稳定性:文字 → fuzzy → fingerprint 三重保险
  - 数据完整性:override 不静默,审计可追
  - path B 衔接基础打实(VPIP/PFR 等统计不会被混乱玩家名 + raise/bet 误识别污染)
- **行为变化**:
  - `player_id_map` 不再 hand 重置 → pipeline 重启才清空(需求约束 — 见 #2)
  - 每手 hand-start 多一次 8x8 image hash 计算 × N seats ≈ 1ms × 8 = ~8ms;不影响 500ms tick
  - fold_area 现在被同时用作 avatar 像素源(原本只 OCR 文字)— 一图二用,无副作用
- **关联待办**:
  - 用户实战验证 4 bundle 效果
  - confidence 分布 SQL 复查
  - 更新 [[cross-validation-architecture-pending]] memory 反映已实施部分

## 9. 违规标注

无。

## Router 第四步自检

- 模式:DEV(用户明示"全做")
- 产出物:4 commits + 本 change-log + 1 push 待执行
- 红线状态:R-7 触发,合规动作均执行;其他 N/A
- pytest:14 passed,无新引入
- 5-min 附带修主动忍住:scope 已大,不再扩
- memory 待更新:[[cross-validation-architecture-pending]] mostly-implemented
