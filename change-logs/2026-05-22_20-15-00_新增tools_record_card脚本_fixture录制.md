# 新增 `tools/record_card.py`：fixture 交互录制脚本

- **完成时间**：2026-05-22 20:15
- **关联需求讨论**：`requirement-discussions/2026-05-22_19-20-00_fixture录制启动方案.md`（pending → 本任务等同于落地方案 B；用户已在对话中 OK 推荐）
- **关联前次 change-log**：`change-logs/2026-05-18_05-15-00_fixture库骨架建立.md`（建装载器和 README 占位时定的格式契约，本脚本照此格式产出）
- **触发红线**：无（脚本设计本身规避了所有可能触发；详见 §5）
- **无关红线已检查**：R-1, R-2, R-3, R-4, R-5, R-6, R-7, R-8, R-9, R-10

## 1. 任务概述

- **用户原始需求**：写一个 Win 端交互式工具，按回车截 hero 卡 → 提示输入 rank+suit → 自动产出符合 `_README.md` 格式的 `<id>.png` + `<id>.json` 对
- **设计 scope（明确不做）**：
  - ❌ 不录 community 卡（`_split_card_regions` 已知 bug，录了反污染）
  - ❌ 不做半自动 recognize-then-confirm（0-baseline 状态识别错率高，价值倒挂）
  - ❌ 不做 GUI（CLI 已足够）
- **涉及功能模块**：新增 `tools/record_card.py` 一文件；不动业务代码

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | 默认 profile = `party_poker` | `rois/` 当前仅此一个 profile；config.py 的 ROI_PROFILE 默认值 "default" 对应文件不存在 |
| 2 | filename 格式 `<rank><suit>_<source>_<NNN>.{png,json}`，NNN 自动取最大已存 +1 | `_README.md` "文件命名建议" 段 |
| 3 | 空白卡检测：std < 10.0 → 跳过 | `pipeline/orchestrator.py` 同款启发式（std > 30 判定有牌；这里用 10 留宽松带） |
| 4 | mss 给的是 BGRA，cv2.imwrite 要 BGR | mss 文档 + 装载器 `tests/test_recognition_fixtures.py` 用 `cv2.imread` 读 BGR |
| 5 | input/output 走 stdin/stdout，PowerShell 直接跑可用 | Python 内置 `input()` 在 PowerShell 一致行为 |
| 6 | KeyboardInterrupt / EOFError 都视作正常退出 + 打印 summary | 用户友好 |
| 7 | 不需写单元测试 | 本脚本是 CLI 交互工具，依赖 mss + Win 窗口 + 真实截图，无法在 Linux pytest 环境跑；smoke 已通过 --help + import + helper 函数验证 |

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `tools/record_card.py` | 新增 | 全文 ~180 行；CLI（argparse）+ 加载 ROI profile + 找 Win 窗口 + 主循环（截 hero 2 张 → 用户输 rank/suit → 落盘 PNG+JSON）+ 输入校验 + 空白卡跳过 + 文件名 auto-sequence |

### 附带修复（5 分钟规则）

无。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更：**否**
- fixture 文件格式与 `tests/fixtures/cards/_README.md` "文件格式" 段完全一致：
  - PNG 文件 ✓
  - JSON 含 `expected.rank` / `expected.suit` ✓
  - JSON 含 `source` 字段 ✓
  - 命名 `<rank><suit>_<source>_<seq>` ✓

## 5. 红线合规动作

**逐条核对**：

- **R-1（牌室 ToS）**：脚本仅用 `mss.grab()` 做屏幕截图，无键鼠注入 / 进程内存访问 / 抓包 / 非白名单 Win hook → ✅ 不触发；ToS 自审责任在用户（party_poker / Poker Master 客户端是否允许 screen capture 类辅助）
- **R-3（手牌数据不外传）**：脚本仅本地写盘，无任何网络 IO；ROI 物理尺寸 ~50×100 px 限定到卡牌区，无法拍到玩家昵称/聊天 → ✅ 不触发
- **R-9（屏幕捕获范围限定）**：脚本只调 `ScreenCapturer.capture_roi`，**不调** `capture_raw`（grep 已确认；`from capture.screen import ScreenCapturer` + 仅 `.capture_roi(roi)` 调用）→ ✅ 不触发

其余 R-2 / R-4 / R-5 / R-6 / R-7 / R-8 / R-10 与本任务无关。

## 6. 测试结果

- **验证路径**：快速验证（CLI 工具 + 无业务逻辑改动 + 无法在 Linux 跑实际截图）

- **smoke 验证**：
  ```
  .venv/bin/python tools/record_card.py --help  →  exit 0，usage 完整
  syntax check                                   →  syntax OK
  import check (动态 importlib)                  →  import OK
  next_seq(empty dir)                            →  1 ✅
  VALID_RANKS, VALID_SUITS 集合                  →  齐全 ✅
  ```

- **全 pytest 基线**：`.venv/bin/pytest tests/ -v`
  ```
  18 tests collected
  14 passed, 4 skipped, 0 failed in 33.25s
  ```
  与前次 baseline 一致 ✅（新脚本不影响测试目标）

- **rules-dev §5.2 判定**：✅ 通过

## 7. 手动操作提醒

⚠️ **Win 测试机用户**：

1. `git pull`（拉到本次 commit 后）
2. 在 PowerShell 跑：
   ```powershell
   cd D:\project\pokemir
   .\.venv\Scripts\python.exe tools\record_card.py
   ```
   不带参数默认用 `party_poker` profile + `pm` source。
3. 操作流程：
   - 先确认 poker 客户端窗口已打开（脚本会找标题含 "Poker Master" 的窗口）
   - 在牌桌发牌让 hero 卡可见
   - 按 Enter → 脚本截两张 hero 卡 → 给每张输 `rank suit`（如 `A h` / `T s`），或 `s` 跳过
   - 重复直到 ≥ 30 张
   - 输 `q` 退出，看 summary
4. 完成后：
   ```powershell
   git add tests/fixtures/cards/*.png tests/fixtures/cards/*.json
   git commit -m "加 N 张 fixture"
   git push
   ```

## 8. 潜在影响范围

- **正向**：
  - 解锁 fixture 录制路径，是项目 P0 瓶颈的具体落地工具
  - 30 张 fixture 到位后：自动激活 R-X 识别精度回归保护红线候选；解锁 `cards.py` 红黑不分 + `_split_card_regions` 边界反转的可验收修复
  - 用户单张录制时间：手动方案 1-2 min → 本脚本 ~30s（4-5× 提速）
- **行为变化**：仅在用户主动跑脚本时；不影响 pipeline runtime / pytest / 任何已有功能
- **关联待办**：
  - 用户在 Win 端跑录制（**必须用户手动**，我无法代劳）
  - 录到 ≥ 30 张 → REQ 讨论激活 R-X 红线
  - 后续 community fixture 录制：需先修 `_split_card_regions` bug，再扩展本脚本（独立任务）

## 9. 违规标注

无。
