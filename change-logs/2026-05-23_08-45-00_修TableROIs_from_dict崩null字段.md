# 修 `TableROIs.from_dict` 在 `pot_size: null` 时崩

- **完成时间**：2026-05-23 08:45
- **关联需求讨论**：无（紧急 P0 bug 修复——用户实测中卡死）
- **关联前次 change-log**：`change-logs/2026-05-23_06-38-00_落地D-revised用户识别方案_OCR平台ID.md`（同模块前次改动；本次是该改动的下游 bug）
- **触发红线**：无
- **无关红线已检查**：R-1, R-2, R-3, R-4, R-5, R-6, R-7, R-8, R-9, R-10

## 1. 任务概述

- **bug 现象**：用户 Win 端跑 `record_card.py`，traceback 在 `capture/roi.py:69` `_tuple_to_roi(data["pot_size"], ...)`，`TypeError: 'NoneType' object is not subscriptable`
- **root cause**：用户按指引在 `roi_config.py` 跳过了 `pot_size`（fixture 录制不需要），导致 JSON 中 `"pot_size": null`；但 `TableROIs.from_dict` 直接 subscript `data["pot_size"]` 不容忍 None
- **范围**：bug 阻塞当前 fixture 录制活动；用户在牌桌实时等待

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | 同样的崩溃模式也会在 `hero_card_1 = None` / `hero_card_2 = None` 出现 | 同行代码 line 67-68 模式一致；预防性顺手兜底 |
| 2 | `community_cards` 列表里**单个元素** None 也需兜底 | `[*None]` 也会崩；顺手加 `if tup:` 检查 |
| 3 | 兜底策略 = 用 dataclass 既定 default（如 `pot_size` default 是 ROIRegion(0,0,120,30)），而非抛 KeyError | record_card.py 只用 hero_card_1/2；其它字段用 default 即可，不强求用户配；下游用到时若 width=0 会被相应代码识别为"未配"|

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `capture/roi.py` | 修改（`TableROIs.from_dict` +5 行；3 处加 `if data.get(...):` 包裹 + 1 处加 `if tup:`）| `hero_card_1` / `hero_card_2` / `pot_size` 三个字段如 JSON 中是 null/缺失，跳过赋值——dataclass 既有 default 接管 |

### 附带修复（5 分钟规则）

`community_cards` 同址兜底 `if tup:` 防御 list 内含 None 元素。属顺手 1 处，未超 5 分钟规则上限。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更：**否**
- ROI JSON schema 未变（仍是 `hero_card_1 / hero_card_2 / pot_size / community_cards / seats`），只是反序列化对 null 更宽容

## 5. 红线合规动作

无触发。R-7 严格读不触发（schema 结构未变，仅 deserialization 容错增强；不影响 `roi_config.py` 写入侧）。

## 6. 测试结果

- **验证路径**：快速验证（单文件 +5 行；只改 deserialization 路径，且测试用例直接复现 bug 场景）

- **smoke**（Python 内联，复现用户错误场景）：
  ```
  data['pot_size'] = None
  trois = TableROIs.from_dict(data)  # 之前崩，现在通
  → pot_size 自动 fall back 到 default ROIRegion(0,0,120,30)
  ```
  ✅

- **pytest**：`14 passed, 4 skipped, 0 failed`（基线一致）

- **rules-dev §5.2 判定**：✅ 通过

## 7. 手动操作提醒

⚠️ **Win 用户**：

1. `git pull`
2. 不用重跑 `roi_config.py`——你之前框的 hero_card_1 / hero_card_2 已在 `rois/party_poker.json` 里，本次只修反序列化
3. 直接跑 `tools\record_card.py` 应该能正常起来

## 8. 潜在影响范围

- **正向**：
  - record_card.py 现在能在用户只配 hero 卡场景下正常启动
  - 未来任何"部分配置"profile 都不再崩（如只配某几个座位）
- **行为变化**：
  - 用户没配的 pot_size 现在用 default ROI(0,0,120,30)；如果 pipeline runtime 启动并真用到 pot_size，会截到 (0,0)-(120,30) 这个屏幕左上角小框 → OCR 得到垃圾 → `latest_pot_bb` 保持 None（已有 `if amount is not None` 检查）→ event.pot_size_bb 保持 None → 数据库 NULL，**符合预期**
- **关联待办**：
  - 用户未来想跑完整 pipeline 时，需补配 pot_size + 所有 seat ROIs（含 id_area）；当前 fixture 路径不需要

## 9. 违规标注

无。
