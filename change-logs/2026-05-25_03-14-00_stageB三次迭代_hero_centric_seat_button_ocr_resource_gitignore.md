# Stage B 三次迭代: seat hero-centric 重命名 + button OCR 检测 + resource/ gitignore

- **完成时间**：2026-05-25 03:14
- **关联需求讨论**：`requirement-discussions/2026-05-25_02-08-00_PathA第4步阶段B_seats×30_ROI配置.md`(confirmed,持续在 stage B 实施过程中)
- **关联前次 change-log**：`change-logs/2026-05-25_02-55-00_stageB二次迭代_fold_area_element粒度参数_提示.md`
- **触发红线**：**R-7（ROI 配置一致性 — seat 标号语义切换 + button 检测算法切换）**;**R-10(本地工件不入 git — resource/)**
- **无关红线已检查**：R-1 到 R-6, R-8, R-9

## 1. 任务概述

用户根据实战截图(`resource/Cardtable.png`)反向校准提出 3 条改进,讨论模式三轮 confirmed 后实施:

1. **DEV-1**: seat 标号从"屏幕物理位置"重命名为"hero-centric clockwise"(seat_0=hero 自己,seat_N=hero 顺时针第 N 个邻居)
2. **DEV-2**: 玩家 D 按钮检测从"亮度阈值启发式"切换到 OCR 识别 "D" 字符(用户报告 WePoker D 标记小但特征清晰)
3. **DEV-3**: `resource/` 目录进 `.gitignore`(本地参考截图,Win/VPS 各自维护,不入版本控制)

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | seat_0 永远是底部正中位置(hero 物理位置),不依赖用户是否真坐桌 | 讨论协商;observer 模式下底部正中就是某玩家的物理座位,只是不是用户本人 |
| 2 | seat_index 语义切换(物理→hero-centric)对 `compute_positions` 模运算无影响 — 它只是个整数索引 | 读 `capture/roi.py::compute_positions`:`(i - button_seat_index) % num_seats`,与命名无关 |
| 3 | WePoker D 按钮在玩家筹码量左侧,大小 ~10-20 像素,字符清晰可被 EasyOCR 识别 | 用户报告"特征非常明显" |
| 4 | `ocr.read_text(img, allowlist='D')` 在小尺寸 ROI 上仍可工作(已在 stage A community card rank 识别中验证 allowlist 路径)| `recognition/ocr.py:42-61` `read_text` 支持 allowlist 参数 |
| 5 | `resource/` 在 Win 和 VPS 端各自存在,**内容可能不同**(各自截图记录) | 用户描述 + 决策 |

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `tools/roi_config.py` | 修改(+25/-12 行)| `_get_seat_labels(6)` 与 `_get_seat_labels(9)` 全部重写为 hero-centric 标签:`seat_0 = "you / bottom-center"`,`seat_N = "N clockwise — ..."`;`ELEMENT_HINTS["id"]` 改文案("玩家昵称区域 — 中文/英文/数字混排");`ELEMENT_HINTS["button_indicator"]` 改文案("玩家筹码量数字**左侧紧贴**的小 D 标记");docstring 标注 2026-05-25 redesigned 历史 |
| `pipeline/orchestrator.py` | 修改(+10/-13 行)| `_detect_button_position` 替换实现:遍历每个 seat 的 `button_indicator` ROI → `ocr.read_text(img, allowlist='D')` → 含 "D" 即为 dealer seat;docstring 更新 |
| `.gitignore` | 修改(+2/-0 行)| 在 `tests/fixtures/_pending/` 后追加 `# local reference snapshots (...)` 注释 + `resource/` pattern |

### 附带修复（5 分钟规则）

无。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更:**否**
- seat_index JSON schema 不变(整数索引);ROI profile 文件格式向前兼容(空 seats 数组与配过的均工作)

## 5. 红线合规动作

**R-7（ROI 配置一致性)** 触发:
- [x] seat 标号语义切换 = additive(无现存 seats 数据可破坏,party_poker.json 是空 seats: [])
- [x] button 检测算法切换 = 函数级 drop-in 替换,签名与调用点不变
- [x] OCR 替代亮度启发式:若 D 按钮 ROI 未配,函数返回 None,fallback 走 "default seat 0" 路径(与原行为一致)

**R-10(本地工件不入 git)** 触发:
- [x] `resource/` 已加入 `.gitignore`
- [x] 该路径下文件不被 git status 跟踪(`git check-ignore -v resource/Cardtable.png` 返回 exit=0 + 命中 `.gitignore:42:resource/`)
- [x] 用户保留对该目录的手动管理权(Win/VPS 端各自维护)

## 6. 测试结果

- **语法**:`tools/roi_config.py` / `pipeline/orchestrator.py` AST parse OK ✓
- **gitignore 验证**:`git check-ignore -v resource/Cardtable.png` exit=0,line 42 匹配 ✓
- **pytest**:14 passed / 3 skipped / 0 failed(与上版本完全一致) ✓
- **rules-dev §5.2 判定**:✓ 通过

未直接 unit-test 的项:
- `_detect_button_position` OCR 路径仅 Win 端有真 D 按钮可测;Linux 端无 GPU + 无 fixture 覆盖,**实测留给 Win 端 stage B 联调**

## 7. 手动操作提醒

⚠️ **Win 端用户(stage B 新配置流程)**:

### A. `git pull`

```powershell
cd D:\project\pokemir
git pull
```

### B. 新命名约定的两个 seats

**新约定**:`seat_0 = hero(你)`,`seat_N = 顺时针第 N 个邻居`。

- 上轮 "seat_4 = bottom-right" → **现在叫 `seat_1`**(你 clockwise 第 1 个 = 右下邻居)
- 上轮 "seat_6 = bottom-left" → **现在叫 `seat_8`**(你 clockwise 第 8 个 = 左下邻居)

```powershell
# 自己右下邻居 (seat_1)
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_1 --element stack --name party_poker
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_1 --element action --name party_poker
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_1 --element fold_area --name party_poker
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_1 --element button_indicator --name party_poker
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_1 --element id --name party_poker

# 自己左下邻居 (seat_8)
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_8 --element stack --name party_poker
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_8 --element action --name party_poker
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_8 --element fold_area --name party_poker
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_8 --element button_indicator --name party_poker
.\.venv\Scripts\python.exe tools\roi_config.py --field seat_8 --element id --name party_poker
```

(cards 元素仍可 ESC 跳;showdown 时偶现的对手底牌,本次不必配。)

### C. `button_indicator` 框选要点

D 按钮在玩家**筹码量数字左侧紧贴**(~10-20 像素小区域):
- 框紧一点,只圈住 D 字符 + 一点点边框
- 不要把筹码量数字也框进来(否则 OCR 会把数字也读出,allowlist='D' 已防,但小区域更稳)

### D. verify

```powershell
.\.venv\Scripts\python.exe tools\roi_config.py --verify --name party_poker
```

应看到 seat_1 / seat_8 各 5 个框(action/fold_area/stack/button/id)。

### E. 跑 pipeline

```powershell
.\.venv\Scripts\python.exe main.py pipeline --profile party_poker
```

挂 5-10 手,贴日志最后 60 行。我远端查 action_events 表。

## 8. 潜在影响范围

- **正向**:
  - seat 标号自然直观(seat_0 = 你 自己)
  - button OCR 比亮度更稳,误判率低
  - resource/ 不污染 git
- **行为变化**:
  - `_detect_button_position` 每手 hand-start 多一次 OCR(~10-30ms × N seats);500ms tick 仍宽裕
  - 旧的"亮度阈值 60" 启发式被移除 — 仅依赖 OCR;若 D 按钮区域被 button_indicator 错配,**会全部 fallback seat 0**(原行为)
- **关联待办**:
  - 用户 Win 端 stage B B2 实际配置 + B3 跑 pipeline
  - 联调 button OCR 准确度
  - 剩余 7 个 seats(后续会话)
  - `_get_seat_labels(6)` 标签同步更新(本次顺手做了),但 num_seats=6 时的具体几何还没用用户截图验证 — 等真用 6 座桌时再校

## 9. 违规标注

无。

## Router 第四步自检

- 模式:DEV(承接讨论模式三轮 confirmed:Q1=seat hero-centric / Q2=button OCR β / Q3=本次 seat_1+seat_8 / resource gitignore)
- 产出物:3 文件改 + 本 change-log + 1 commit 待推
- 红线状态:R-7 + R-10 触发,合规动作均执行;其他 N/A
- pytest:14 passed,无新增 fail
- 5-min 附带修主动忍住:`_get_seat_labels(6)` 几何还没用户截图校,但本次顺改约定,留待真用 6 座再深究
