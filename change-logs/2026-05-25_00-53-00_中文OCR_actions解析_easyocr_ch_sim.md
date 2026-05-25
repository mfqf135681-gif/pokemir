# 中文动作 OCR 支持: actions.py 加中文 keyword + easyocr 加 ch_sim 模型

- **完成时间**：2026-05-25 00:53
- **关联需求讨论**：`requirement-discussions/2026-05-25_00-45-00_PathA第4步_实战数据采集.md`（confirmed,阶段 A 实施）
- **关联前次 change-log**：`change-logs/2026-05-25_00-42-00_PathA第3步pipeline_smoke_accepted.md`
- **触发红线**：**R-8（识别模型加载链）**——EasyOCR 加 ch_sim 模型 → change-log §7 提示 Win 用户首次启动下载
- **无关红线已检查**：R-1 到 R-7, R-9, R-10

## 1. 任务概述

Path A 第 4 步阶段 A 第一项工作: 让 WePoker 的中文动作文字(`跟注/加注/弃牌/...`)能被 pipeline 正确识别为 `ActionType`。

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | WePoker 动作文字常见中文词:`跟注 加注 下注 弃牌 过牌 全下 小盲 大盲 看牌 盖牌 让牌 全押 全压` | 行业通用 + 我对 WePoker 等中文牌室 UI 的认知 |
| 2 | 加 `ch_sim` 不会破坏现有英文 OCR(包括 card rank 的 allowlist 路径)| EasyOCR 多语言模型独立加载,allowlist 仍生效 |
| 3 | `.upper()` 对中文字符是 no-op | Python 字符串 upper() 仅影响 ASCII;中文匹配在 upper 后仍工作 |
| 4 | 模型 ~50MB,装在 [[image-only-compliance-constraint]] 配的项目内 `.cache/easyocr/` | EASYOCR_MODEL_DIR 早就重定向 |
| 5 | ch_sim 加载会让 OCR 调用稍慢(model size 增加)但 5070 Ti GPU 路径影响极小 | 经验:多语言 reader 比单语言只慢 5-10% |

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `recognition/actions.py` | 修改（+11/-3 行;parser 加中文分支)| `parse()` 在每个 ActionType 检测段加中文 keyword(`跟注 加注 下注 弃牌 过牌 全下 小盲 大盲 看牌 盖牌 让牌 全押 全压 前注`);保留英文路径全部兼容;docstring 更新含中文 |
| `recognition/ocr.py` | 修改（+5/-2 行）| `easyocr.Reader(['ch_sim', 'en'])` 替代 `(['en'])`;ch_sim 在前(主语言)更稳;加注释解释 |

### 附带修复（5 分钟规则）

无。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更：**否**
- `ActionType` enum 不变;只是更多 OCR text 能映射到相同 ActionType

## 5. 红线合规动作

**R-8 触发**——按 .agents/project-constraints.md R-8 合规动作:
- [x] 修改 `recognition/*` 模型加载相关代码(`easyocr.Reader(['ch_sim', 'en'])`)
- [x] change-log §7 提示用户**首次启动会下载 ~50 MB ch_sim 模型**,确保 `POKEMIR_EASYOCR_DIR` 环境变量 已设置(项目内 `.cache/easyocr/`)
- [x] 与 [[image-only-compliance-constraint]] 不冲突:仍本地下载 + 本地推理,无 DOM/插件

## 6. 测试结果

- **验证路径**：完整验证(代码改 + 跨模块)

- **smoke**(Linux):11 个 case 全过 ✓
  ```
  ✓ '跟注 100' → CALL amount=100.0
  ✓ '加注 300' → RAISE amount=300.0
  ✓ '弃牌' → FOLD
  ✓ '过牌' → CHECK
  ✓ '下注 50' → BET amount=50.0
  ✓ '全下' → ALL_IN
  ✓ '小盲 5' → POST_SB amount=5.0
  ✓ '大盲 10' → POST_BB amount=10.0
  ✓ 'FOLD' → FOLD (English 仍工作)
  ✓ 'CALL $2.50' → CALL amount=2.5 (English with $)
  ✓ '看牌' → CHECK (alt 中文)
  ```

- **pytest sanity**: 14 passed / 3 skipped / 0 failed ✓

- **rules-dev §5.2 判定**：✓ 通过

## 7. 手动操作提醒

⚠️ **Win 用户首次启动 pipeline 时**:

1. `git pull` 拉本次改动
2. 第一次跑 `python main.py pipeline` 时,EasyOCR 会**自动下载 `ch_sim` 模型(~50 MB)**到 `D:\project\pokemir\.cache\easyocr\`
3. 下载需要网络;下载完成后**永久缓存**,后续启动免下
4. 启动日志会显示模型加载(注意是否报错"Downloading ch_sim ...")
5. **测试**:跑 pipeline 观战,看 PowerShell 是否出现:
   ```
   Action: <name>(<position>) call <amount> [<street>]
   ```
   这就证明中文动作 OCR + parser 工作

### ⚠️ 与 Sub-REQ 阶段 A 第二项 (PG 装机) 并行

本次改动**不依赖 PG 装机** —— 你可以现在装 PG + 测中文 OCR,**两件事同时做**。但如果 PG 还没装,实战会:
- 中文 OCR 跑通 + 解析正确 ActionType
- 仍 no-db mode → action_event **不落库**(only 日志)

→ 想真正验证数据**入库**,PG 装机也必须做。

## 8. 潜在影响范围

- **正向**：
  - WePoker 动作文字(中文)被 pipeline 正确识别
  - 英文路径完全保持(法/俄/其他多语言场景兼容)
  - 多种同义词覆盖(如 `过牌`/`看牌`/`让牌` 都 → CHECK)
- **行为变化**：
  - OCR 调用稍慢(中文模型加载到内存 + 推理时间 +5-10%)
  - 模型缓存目录 `.cache/easyocr/` 多一个 `chinese_sim_g2.pth` 等文件(~50 MB)
- **关联待办**：
  - Sub-REQ 阶段 A 第二项:Win 用户装 PG(本次 commit 后我给独立指引)
  - Sub-REQ 阶段 B(seats × 30 ROIs)等阶段 A 全通后再做

## 9. 违规标注

无。
