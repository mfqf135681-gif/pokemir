# Card recognition fixtures

> Fixture-driven 识别精度回归测试样本库。装载器：`tests/test_recognition_fixtures.py`

---

## 用途

让识别相关代码（`recognition/cards.py` / `recognition/ocr.py` / `recognition/vision.py`）的改动**可量化对照**：

- 改前：识别率 67%
- 改后：识别率 73% → 真实提升 +6%
- 改后：识别率 60% → 引入回归 -7%（必须修复或 REQ 例外）

替代"凭肉眼判断改对没有"的模糊评估。

---

## 文件格式

每张 fixture 是一对 `<id>.png` + `<id>.json` 同目录文件：

### `<id>.png`

真实 poker 客户端截图中**裁出的单张卡牌区域**（建议 60–120 px 宽，按客户端实际 ROI 尺寸）。

### `<id>.json`

```json
{
  "expected": {"rank": "A", "suit": "h"},
  "source": "GGPoker 6max 0.05/0.10",
  "notes": "客户端版本 v25.10；2026-05-18 录"
}
```

字段说明：

| 字段 | 必填 | 说明 |
|:---|:---:|:---|
| `expected.rank` | ✅ | `2-9` / `T` / `J` / `Q` / `K` / `A` |
| `expected.suit` | ✅ | `s` 黑桃 / `h` 红心 / `d` 方块 / `c` 梅花 |
| `source` | ⬜ | 哪个客户端/桌子录的（用于排查识别率差异） |
| `notes` | ⬜ | 任何上下文（光照/缩放/客户端版本/异常情况） |

---

## 文件命名建议

`<rank><suit>_<source>_<seq>.{png,json}` 形如：

- `Ah_gg_001.png` / `Ah_gg_001.json`
- `Td_ps_042.png` / `Td_ps_042.json`
- `2c_synth_001.png` / `2c_synth_001.json`（合成图打 _synth_ 前缀）

---

## 录制流程（Win 测试机）

1. 在 poker 客户端打开实际牌桌
2. 用 `tools/roi_config.py --verify --name <profile>` 找出 hero 卡牌 ROI 坐标
3. 跑一个简单截图脚本（或手动 mss）按 ROI 裁出单卡 PNG
4. 标注 JSON 文件
5. SCP 或直接 git commit + push 到仓
6. 下次 `bash tools/sync-from-vps.sh`（Linux 端）拉到所有 dev 机器

⚠️ **录制隐私注意**：截图前**移除/挡住**任何玩家昵称、聊天框、桌号等可识别信息——本仓 fixture 进 git 不应含个人/对手身份信息。仅裁卡牌区域。

---

## 运行

```bash
.venv/bin/pytest tests/test_recognition_fixtures.py -v
```

每个 fixture 是一个独立 parametrized test。pytest 输出形如：

```
tests/test_recognition_fixtures.py::test_card_fixture[Ah_gg_001] PASSED
tests/test_recognition_fixtures.py::test_card_fixture[Td_ps_042] FAILED   # rank mismatch
tests/test_recognition_fixtures.py::test_card_fixture[2c_synth_001] SKIPPED  # recognizer returned None
```

整体识别率从 pytest 退出码后的 summary 看：`8 passed, 3 failed, 2 skipped` → 8/13 = 61.5%。

---

## 与红线 R-8 联动（未来）

当 fixture 数量 ≥ 30 张稳定基线后，可激活 `R-8 候选`（`.agents/project-constraints.md §8.4` 待激活红线 R-10 占位）：

> 识别精度回归保护：识别模块的修改不得让 `tests/fixtures/cards/` 整体识别率下降超过 2%。

激活时机：用户在 Win 端录够 30 张 + 跑一次 baseline 测得当前识别率 → REQ 讨论确认 baseline → 升红线。

---

## 当前状态

- 骨架：✅ 装载器 + 文档 + 目录结构就位
- 真实 fixture：⬜ 0 张（等 Win 端录制）
- pytest collect 此目录：当前 0 个 parametrized test（不影响基线 14p/3s/0f）
