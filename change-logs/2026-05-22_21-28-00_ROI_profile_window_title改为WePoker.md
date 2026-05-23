# `rois/party_poker.json` 改 `window_title` = "WePoker"

- **完成时间**：2026-05-22 21:28
- **关联需求讨论**：本会话上文用户澄清"客户端为 H5 网页 WePoker"（REQ 快速路径，无独立 doc）
- **关联前次 change-log**：`change-logs/2026-05-22_20-15-00_新增tools_record_card脚本_fixture录制.md`（record_card.py 依赖此 profile 找窗口）
- **触发红线**：无（R-7 严格读未触发——仅字段值改动，schema 结构未变）
- **无关红线已检查**：R-1, R-2, R-3, R-4, R-5, R-6, R-7, R-8, R-9, R-10

## 1. 任务概述

- **用户原始需求**："客户端修正为 H5 网页 / Chrome / WePoker网页版｜免下载快速进入"
- **背景**：早期 `rois/party_poker.json` 假设客户端为原生 "Poker Master"，但用户实际用 Chrome 跑 WePoker 网页版。`find_window_by_title` 必须能匹配实际浏览器窗口标题，否则 `tools/record_card.py` 报"找不到窗口"
- **本次 scope**：仅改 `window_title` 字段值。ROI 坐标因坐标系完全不同（浏览器布局 vs 原生客户端）也无效，但**留给用户用 `roi_config.py` 重配**——不在本次 DEV scope（重配是用户手动操作）

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | `window_title` 用 `"WePoker"` 子串足够 | `find_window_by_title` 是子串匹配（`title_substring.lower() in title.lower()`，capture/screen.py:73）；"WePoker" 是 Chrome 标签栏标题里最稳定的核心识别词；用户给的完整字串 `WePoker网页版｜免下载快速进入` 含全角符号 `｜`，子串方案对站方加 suffix / 改 separator 更鲁棒 |
| 2 | 不改 `name` 字段（仍叫 "party_poker"） | name 字段不影响 find_window_by_title；改了会引入额外文件改动；rename 文件涉及更新 `tools/record_card.py` 默认 `--profile party_poker` + 用户已有的命令习惯，scope 蔓延 |
| 3 | 不动 ROI 坐标 | 旧坐标对 WePoker 完全无效；保留作 schema placeholder（避免 `from_json` 必填字段 KeyError）；用户跑 `roi_config.py --name party_poker` 时会自动覆盖 |
| 4 | 不需删除 / 弃置 `party_poker.json` 文件 | 文件名虽误导但功能正常；rename 是独立 cleanup 任务 |

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `rois/party_poker.json` | 修改（-1/+1） | `"window_title": "Poker Master"` → `"window_title": "WePoker"` |

### 附带修复（5 分钟规则）

无。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更：**否**
- ROI profile schema 字段未增删，类型未变，仅值变 → 不涉及契约

## 5. 红线合规动作

R-7 严格读未触发（结构未变），但**精神上需要 §7 提示**：旧 ROI 坐标对 WePoker 无效，用户必须重跑 `roi_config.py` 才能录 fixture。已在 §7 标注。

## 6. 测试结果

- **验证路径**：快速验证（单文件单字段值改动；ROI profile JSON 不被 pytest 直接验证）

- **执行**：`.venv/bin/pytest tests/ -q`
  ```
  14 passed, 4 skipped, 10 warnings in 34.36s
  ```
  与基线一致 ✅

- **rules-dev §5.2 判定**：✅ 通过

## 7. 手动操作提醒

⚠️ **Win 用户在 Chrome 打开 WePoker 牌桌后**：

1. 先确认 Chrome 窗口标题包含 "WePoker" 字串（窗口最顶上一行；Chrome 标签栏内容会反映在窗口标题）
2. 跑 ROI 重配（**必须做，旧坐标无效**）：
   ```powershell
   .\.venv\Scripts\python.exe tools\roi_config.py --name party_poker
   ```
   - 脚本会截当前 Chrome 窗口
   - 顺序弹出 7 个 ROI 选择窗口：hero_card_1 / hero_card_2 / community_1-5 / pot_size
   - 每个弹窗：鼠标拖框选目标区域 → 按 **空格** 确认 / 按 **C** 跳过
   - 录 fixture **只需要** hero_card_1 + hero_card_2 两个准；其余可全 C 跳过（fixture 路径不依赖它们）
3. 重配完检查：
   ```powershell
   .\.venv\Scripts\python.exe tools\roi_config.py --verify --name party_poker
   ```
   弹窗显示截图 + 各 ROI 框；目测对得上就 OK
4. 准了之后跑录制：
   ```powershell
   .\.venv\Scripts\python.exe tools\record_card.py
   ```

## 8. 潜在影响范围

- **正向**：`record_card.py` + 任何后续 pipeline 跑时 `find_window_by_title` 能匹配实际 WePoker Chrome 窗口
- **行为变化**：旧 ROI 坐标对 WePoker 无效，用户重配前 `record_card.py` 会截到空白区（脚本会显示 "空白区，跳过" 并不污染 fixture）
- **关联待办**：
  - 用户在 Win 端跑 `roi_config.py` 重配 hero_card_1 + hero_card_2（**手动操作**）
  - 后续如果需要严格匹配（避免误中其它含 "WePoker" 字串的窗口），可升级 substring 为更具体的串

## 9. 违规标注

无。
