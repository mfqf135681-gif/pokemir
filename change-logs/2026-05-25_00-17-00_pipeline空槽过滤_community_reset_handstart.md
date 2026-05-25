# pipeline 加空槽过滤 + community-reset 触发新 hand (观战模式补丁)

- **完成时间**：2026-05-25 00:17
- **关联需求讨论**：`requirement-discussions/2026-05-24_20-54-00_PathA第3步_Win端pipeline全链路smoke.md`（pending,迭代中）
- **关联前次 change-log**：`change-logs/2026-05-24_21-12-00_pipeline修_extract_amount_加no_db自动降级.md`
- **触发红线**：无
- **无关红线已检查**：R-1 到 R-10 全无触发

## 1. 任务概述

Path A 第 3 步 A 探底第 3 轮,用户实测两个核心问题:

| # | 现象 | 根因 | 修法 |
|:---:|:---|:---|:---|
| 1 | 屏幕只发 flop 3 张但 pipeline 报 `Street river: ['5s','2d','5d','3s','3s']` 5 张(最后 2 张乱猜) | community_3/4 ROI 看的是空槽位（绿桌面）,CNN 没学过空槽位,在垃圾像素上塌缩到固定预测 | 在 `_process_community_cards` 中**喂 CNN 前先用亮度启发式判断槽位是否有牌** |
| 2 | 多手都没新 hand 触发,pipeline 永远在初始 hand 跑 | 观战时 hero 无卡 → hero ROI 落 dataclass 默认 `(0,0,60,80)` = Chrome 左上 = UI 稳定 → `check_hero_cards` 哈希永不变 → 不触发新 hand | 在 `_tick` 加**观战模式 fallback**: community count 从 > 0 跌到 0 时(新一局发牌前清场)触发新 hand |

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | 空槽用平均亮度 > 150 判断有卡,效果够用 | WePoker 卡牌背景白色(luminance ~230),桌面绿色(~70-100);150 为安全阈值 |
| 2 | community 5→0 是新一手开始的可靠信号 | 真实扑克流程:river 5 张 → 下一手开始时 community 清零 → 然后发 preflop / flop |
| 3 | 此 fallback 与原 hero-card-change 触发**互补不冲突** | 真实坐桌玩家两个信号都生效(hero 变化 OR community 重置先到的触发);观战只有 community 信号工作 |
| 4 | 不需要 CLI 模式开关 | 两个信号都执行,先到的触发,自动适配 |

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `pipeline/detector.py` | 修改（+10 行）| `check_community_change` 增加 `_community_just_reset` 内部 flag(只要 count 从 >0 跌到 0 就置 True);新增 `community_just_reset()` 公共方法供 orchestrator 查询 |
| `pipeline/orchestrator.py` | 修改（+18 行）| (a) `_process_community_cards` 调 CNN 前调 `_slot_has_card(img)` 过滤空槽位;(b) `_tick` 在 community 处理之后增加观战模式 fallback: `if has_active_hand and tracker.community_just_reset()` 则 `_end_current_hand` + `_start_new_hand`;(c) 新增静态方法 `_slot_has_card(img)` 用 BGR 平均亮度 > 150 判断 |

### 附带修复（5 分钟规则）

无。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更：**否**
- StateTracker 公共 API 加 `community_just_reset()` 但不动既有方法

## 5. 红线合规动作

无触发。

## 6. 测试结果

- **验证路径**：完整验证（多文件 + 行为变化）

- **smoke 1: 空槽过滤**:
  - 白色像素图(模拟卡背景) → `_slot_has_card` 返回 `True` ✓
  - 绿色像素图(模拟空桌) → 返回 `False` ✓
- **smoke 2: community reset 检测**:
  - 3 cards 后: just_reset = False ✓
  - 清空后(模拟新一手): just_reset = True ✓
- **pytest sanity**: 14 passed / 3 skipped / 0 failed ✓

## 7. 手动操作提醒

⚠️ **Win 用户**:
1. `git pull`
2. 重跑 `python main.py pipeline`,继续观战
3. 期望新行为:
   - `Street flop: [3 张不同卡]`（不再有 3s,3s 这种空槽乱猜）
   - 每次新一手发牌时打印 `[INFO] Community reset detected → starting new hand (observer mode)`
   - 跟着打 `[INFO] New hand started: <新 uuid>`
4. 跑 3-5 分钟,**横跨 2-3 手**,贴日志给我

## 8. 潜在影响范围

- **正向**：
  - 观战模式 pipeline 完整跑通(hand → flop → turn → river → 新 hand 闭环)
  - 空槽不再误识别 → 数据干净
- **行为变化**：
  - 真实玩家模式行为不变(hero 变化先触发 → 走原路径;community reset 后到 → 该手已结束,fallback noop)
  - 观战模式从"卡死"变成"正常循环"
- **关联待办**：
  - pot_size ROI 仍 null → pot 识别不工作(下次配 ROI 时框上)
  - seats 仍 [] → 玩家动作识别不工作(后续阶段,需要中文 OCR 模型)
  - hero 在观战模式下伪 ['9c','9c'] 仍会出现(cosmetic 噪声,不影响)

## 9. 违规标注

无。
