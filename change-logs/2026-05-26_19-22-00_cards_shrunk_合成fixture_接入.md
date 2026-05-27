# Cards 合成缩小版 fixture + trainer 接入(免费 +5-10% conf)

- **完成时间**:2026-05-26 19:22
- **关联前次 change-log**:`change-logs/2026-05-26_18-09-00_label_showdown_手动截图_auto_split.md`
- **关联讨论**:用户问"老数据按比例缩小用于训练效果如何",论证 shrink aug 补 trainer 没覆盖的"detail loss"盲区,且零副作用

## 1. 任务概述

诊断:摊牌 CNN rank_conf 卡在 0.65-0.85,**suit 已经满分**。残留瓶颈是**牌图尺寸**(摊牌区比 community 小 30-50%)→ rank 数字像素少。

方案:用现有 212 张 community fixture **下采样再上采样**(BILINEAR 双向),生成"尺寸损失但维度不变"的合成样本作为训练增量。**不替代真实摊牌数据**,作为零成本预热 / class 全覆盖兜底。

策略:
- 原始 `tests/fixtures/cards/` **完全不动**(用户明确说"老数据有保留价值")
- 新建 `tests/fixtures/cards_shrunk/` 放合成版,224 张 = 212 × 2 scales(0.5×, 0.7×)
- 标记为 **community 域,train-only**(不进 val,避免虚高 community val_acc)

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | rank conf 主要瓶颈 = 牌图尺寸损失(高频细节丢失)| 16:47-17:07 diag 数据 suit=0.96 / rank=0.74,典型尺寸劣化症状 |
| 2 | BILINEAR 双向 roundtrip 模拟 WePoker 摊牌区 downscale 路径 | trainer Resize 也是 BILINEAR,一致 |
| 3 | 2 个 scale(0.5, 0.7)覆盖 conf 散布范围(对应不同 seat 的视觉尺寸)| 工程经验 |
| 4 | 合成样本不应进 val — 否则 val_community_acc 会被"易识别"的合成样本拉高,失去信号意义 | 标准 ML 实践 |

## 3. 文件变更清单

| 文件 | 变更 |
|---|---|
| `tools/shrink_cards.py` ✨**新建** | 生成器:遍历 `cards/*.png` × `*.json`,对每张执行 BILINEAR resize(W×s, H×s) 再 resize(W, H);保存到 `cards_shrunk/<stem>_s5.png` / `_s7.png` + 同名 JSON(JSON 加 `synthetic: true`, `source_fixture`, `shrink_scale` 字段);CLI 支持 `--scales`/`--dry-run`/`--clean` |
| `tests/fixtures/cards_shrunk/` ✨**新建** | 424 个 PNG + 424 个 JSON;原始 cards/ 212 张零改动 |
| `tools/train_card_cnn.py` | + `SHRUNK_DIR` 常量;CardDataset 5-tuple 加 `is_train_only` 标记;读 `cards_shrunk/` 时 is_train_only=True;`__getitem__` 解 5 元;main() 拆 train/val 时合成样本**全部进 train**,not val |

## 4. 验证

- ✅ 424 张合成文件生成成功
- ✅ orig vs s5 pixel mean diff = 10.1(明显糊),orig vs s7 = 6.6(温和糊)— 视觉劣化梯度合理
- ✅ 维度不变(61×89,与原图同),只是 detail 损失
- ✅ CardDataset 总样本 636 = 212 真 + 424 合成,全部 community 域,424 标记 is_train_only=True
- ✅ Train/val split 测试:train=594(含 424 合成),val=42(**0 合成** — 干净信号)

## 5. 用户后续操作

```powershell
cd D:\project\pokemir
git pull   # 拉 trainer 改动 + 424 张合成 fixture
.venv\Scripts\python.exe -m pytest -q   # 验证 229 仍 pass
# (后续) 你录满 100 张真实摊牌 + 标完后:
.venv\Scripts\python.exe tools\train_card_cnn.py --epochs 80
# 预期:
#   - val community both_acc 仍 ≥99%(干净 val)
#   - val showdown both_acc 提升幅度可观(真实 + 合成 + JPEG/perspective aug 三重)
#   - 推理时摊牌 conf 大幅提升
```

## 6. 风险

- ⚠️ 合成 != 真实 — WePoker 渲染可能有 subpixel 抗锯齿 / 字体 hint 差异,合成捕获不到
  - 缓解:你仍在录 100 张真实摊牌,**合成是兜底,真实是主菜**
- ⚠️ 424 张新文件增加 ~3MB 仓库体积 — 一次性,可接受
- ⚠️ 若 shrink 太激进(0.5×)反而引入噪声 → epoch 实测验证;不行 `--scales 0.6 0.75` 重生成

## 7. 不在 scope

- ❌ 在线 shrink aug(_RandomShrink transform)— offline 已够;在线版可后续追加
- ❌ NONCARD 数据收集 — 等用户录满 d 键标完再说

## 关联记忆

- 无新增 — 这是 [[recognition-stack-production-ready]] 的 fine-tune,主线不变
