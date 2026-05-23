# 新增 `tools/diagnose_recognition.py`：人工核验诊断工具

- **完成时间**：2026-05-23 10:23
- **关联需求讨论**：用户对话中提议（REQ 快速路径，无独立 doc）："把识别结果设置为文件名，我手工校验"
- **关联前次 change-log**：`change-logs/2026-05-23_10-17-00_识别Stage1_rank多crop多策略_OCR allowlist.md`（Stage 1 改动 + 同模式 § 9 标注违例）
- **触发红线**：无
- **无关红线已检查**：R-1, R-2, R-3, R-4, R-5, R-6, R-7, R-8, R-9, R-10

## 1. 任务概述

- **用户原始需求**：每张 fixture 跑识别 → 把识别结果作为文件名复制 PNG → 用户人眼比对图片内容 vs 文件名差异 → 直觉判断哪张错
- **设计**：脚本不动原始 fixture（保留 ground truth）；复制到 `_diagnosis/` 子目录（fixture 装载器自动跳过 `_` 前缀）；文件名格式 `<rec_label>_<seq>.png` 或 `NONE_<seq>` 表示识别失败
- **同时提交本次 diagnostic 输出**：让用户直接 git pull 看，免他在 Win 端再跑一遍命令

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | 输出目录 `_diagnosis/` 用 `_` 前缀避免被装载器误扫 | `tests/test_recognition_fixtures.py:27` 已 filter `if not p.name.startswith("_")` |
| 2 | 文件名编排 `<rec>_<NNN>.png` 保唯一 + 易读 | 多个 fixture 可能同识别结果（如多张 Tc）；序号去重 |
| 3 | 同时 commit 输出的 31 张 PNG（≈ 几百 KB）合理 | 用户拉一次看一次；不影响仓体积；后续可加 .gitignore（本次不做以保留首次诊断） |
| 4 | 脚本不依赖 fixture 文件名里的"答案"做识别（避免循环验证）| 识别只用图像内容；filename 只用于汇总打印的"vs expected"对照 |

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `tools/diagnose_recognition.py` | 新增 | 全文 ~80 行；遍历 fixture PNG → 调 CardRecognizer → 复制到 `_diagnosis/` 命名为识别结果 |
| `tests/fixtures/cards/_diagnosis/*.png` | 新增 31 个文件 | 本次诊断快照；用户用来人眼校验 |

### 附带修复（5 分钟规则）

无。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更：**否**
- 不动 fixture 装载器、不动业务代码

## 5. 红线合规动作

无触发。仅添加新工具 + 衍生数据。

## 6. 测试结果

- **验证路径**：快速验证（新增工具，无业务代码改动）

- **执行**：`.venv/bin/python tools/diagnose_recognition.py`
  ```
  Summary: 15 correct / 13 wrong / 3 unrecognized (out of 31)
  Accuracy: 48.4%
  ```
  与 pytest 基线数字一致 ✅（15p / 13f / 3 skip）

- **rules-dev §5.2 判定**：N/A（无业务代码改动；pytest 状态从 Stage 1 继承——已在 Stage 1 change-log §9 显式标注违例并说明理由）

## 7. 手动操作提醒

⚠️ **Win 用户**：
1. `git pull`
2. 文件浏览器打开 `D:\project\pokemir\tests\fixtures\cards\_diagnosis\`
3. 把视图切到 "大图标" 或 "中等图标"
4. 看每张图 vs 文件名：filename 写 `Ah_020.png` → 图看上去是 红心 A 吗？如果不是 → 识别错
5. 标黑桃为梅花、钻石为红心、Q 为 9 等错配你会直接看见
6. 校验完跟我说有几张明显看错——我据此修 Stage 2（suit 判定）

## 8. 潜在影响范围

- **正向**：
  - 用户能离线、自助验证识别质量
  - 减少 Linux/Win 来回沟通的诊断成本
  - 未来改 recognizer 后再跑一次脚本就能直观对比 before/after
- **行为变化**：无运行时变化
- **关联待办**：
  - 后续 Stage 2 完成后再跑一次脚本生成新一版 `_diagnosis/`，commit 替换；或者加 `_diagnosis/` 到 `.gitignore` 改为本地生成
  - 工具可扩展：加 `--side-by-side` 选项把 expected vs recognized 并排出图

## 9. 违规标注

无（Stage 1 的 §9 仍生效；本任务无新增违例）。
