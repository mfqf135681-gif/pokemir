# `main.py` 加 `--profile` 参数 + 默认 ROI 改 `party_poker`

- **完成时间**：2026-05-24 20:59
- **关联需求讨论**：`requirement-discussions/2026-05-24_20-54-00_PathA第3步_Win端pipeline全链路smoke.md`（pending，本 commit 解锁 A 探底第一个崩溃）+ 用户对话中关于"多开是否提前设计"的 REQ 快速讨论
- **关联前次 change-log**：`change-logs/2026-05-24_12-25-00_识别栈完整闭环成果总结.md`
- **触发红线**：无（仅 CLI 接口 + 默认值改动）
- **无关红线已检查**：R-1 到 R-10 全无触发

## 1. 任务概述

- **bug 现象**：用户 Win 端跑 `python main.py pipeline` 立即崩,traceback:
  ```
  FileNotFoundError: [Errno 2] No such file or directory: './rois/default.json'
  ```
- **root cause**：`config.py:34` 中 `ROI_PROFILE` 默认值为 `"default"`,但项目实际 profile 文件名是 `party_poker.json` —— 默认值从未对齐项目现状
- **顺手解决**：用户对话明示"多开问题先不设计架构,但保留扩展余地" → 加 `--profile` CLI 参数,既解决当前 bug,也为未来多桌（每桌一 profile）铺平路径

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | 用户当前只有 `party_poker.json` 一个 profile,默认改成它最实用 | git 中 `rois/` 目录只此一个 |
| 2 | 加 `--profile` CLI 不破坏向后兼容 | 默认 None → 使用 config.py 的默认（party_poker）→ 兼容现有行为 |
| 3 | `PipelineOrchestrator.__init__(roi_profile=None)` 早就支持 profile 参数 | 之前 review 时已确认 signature |
| 4 | 用 argparse 替代裸 `sys.argv` 是 dev-rules 鼓励的（清晰 + 自带 --help） | rules-dev §3 "代码可维护性" |

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `main.py` | 重写（+13 / -10 行） | 从裸 `sys.argv` 切到 argparse;加 `command` positional (api/pipeline,默认 api) + `--profile` 可选;pipeline 模式时把 profile 传给 `PipelineOrchestrator(roi_profile=...)` |
| `config.py` | 修改（1 行）| `ROI_PROFILE` 默认值 `"default"` → `"party_poker"`;env var `POKEMIR_ROI_PROFILE` 仍可覆盖 |

### 附带修复（5 分钟规则）

无。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更：**否**
- CLI 接口是项目外部接口,但不在 contracts/api.yaml 范围（那个是 HTTP API 契约）

## 5. 红线合规动作

无触发。CLI 参数 / 默认值改动不涉及任何红线。

## 6. 测试结果

- **验证路径**：完整验证（CLI 接口改 + 多文件）

- **smoke 1**：`python main.py --help` → 完整 usage 显示 ✓
- **smoke 2**：`python main.py pipeline --profile foo` import 路径无错 ✓ (pipeline 自己会因为 foo.json 不存在崩,但 CLI 解析 OK)
- **pytest sanity**：`pytest tests/ -q --ignore=tests/test_recognition_fixtures.py` → 14 passed / 3 skipped / 0 failed ✓
- **fixture pytest 没跑**因为 Linux CNN model file 不存在(用户重训模型在 Win 端);影响 0

- **rules-dev §5.2 判定**：✓ 通过（非 fixture 路径全过；fixture 路径需 Win 模型,与本次 CLI 改动无关）

## 7. 手动操作提醒

⚠️ **Win 用户**:
1. `git pull` 拉本次改动
2. **重新跑 pipeline**:
   ```powershell
   .\.venv\Scripts\python.exe main.py pipeline
   ```
   这次默认会找 `rois/party_poker.json` —— 你已配过的那个 → **应该过 ROI 加载阶段,推进到下一个崩溃点**
3. 仍然崩的话,贴新 traceback 给我（**预期会**——seats 还是空 + 没 PG + 没中文 OCR 模型）

## 8. 潜在影响范围

- **正向**：
  - pipeline 默认就能找到你那个 profile,**path A 第 3 步 A 探底解锁**
  - 多开预埋:未来 `--profile wepoker_table_1`、`--profile wepoker_table_2` 跑多个进程,各自独立桌
  - argparse 提供 `--help`,日后扩展（如 `--log-level`、`--dry-run` 等）零改动
- **行为变化**：
  - 默认 ROI 从虚构的 "default" 变成实际存在的 "party_poker"
  - 旧 env var `POKEMIR_ROI_PROFILE` 仍生效（向后兼容）
- **关联待办**：
  - 用户立即重试 pipeline,收集下一层 traceback
  - 多开真要做时:每桌一个 profile JSON + 每个 main.py 进程各自 `--profile`

## 9. 违规标注

无。
