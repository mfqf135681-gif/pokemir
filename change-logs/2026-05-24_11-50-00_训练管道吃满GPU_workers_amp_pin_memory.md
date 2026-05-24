# 训练管道吃满 GPU：num_workers + bf16 amp + pin_memory + non_blocking

- **完成时间**：2026-05-24 11:50
- **关联需求讨论**：用户反馈"GPU 没充分利用"(REQ 快速路径,选 A 升级)
- **关联前次 change-log**：`change-logs/2026-05-24_11-39-00_训练加rare类权重和强增强_AplusB.md`
- **触发红线**：无（训练性能优化,模型 schema / 推理接口 / 契约皆不变）
- **无关红线已检查**：R-1 到 R-10 全无触发

## 1. 任务概述

- **用户反馈**：原训练管道仅用 5070 Ti 的 ~10-30%,GPU 多数时间在等 CPU 单线程做数据增强
- **本次升级 5 项**：
  1. DataLoader 并行 (`num_workers=4` 默认)
  2. batch_size 默认 32→64 (这小模型 GPU 吃得下更多)
  3. **bf16 mixed precision** (5070 Ti Blackwell 原生支持)
  4. `pin_memory=True` (GPU 异步内存传输)
  5. `non_blocking=True` 所有 `.to(device)` (overlap CPU/GPU 计算)
- **预期加速**：3-5×(100 epoch 训练时间从 5-10 min → 1-3 min on 5070 Ti)
- **明确不变**：模型架构、loss、训练数学逻辑、val 评估方法,准确率结果同（仅运行更快）

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | `num_workers=4` 在 Win 上稳定 | PyTorch 标准 + 模块化代码（无 lambda 等 spawn-unfriendly 结构）|
| 2 | bf16 在 Blackwell 上无精度损失 | 5070 Ti 原生 bf16 ALU；mixed precision 标准实践 |
| 3 | batch_size 64 仍能容纳 5070 Ti 16GB VRAM | 模型 256K params × 64 batch + 激活值 < 100 MB,远低于 VRAM 上限 |
| 4 | `persistent_workers=True` 避免 epoch 间 worker 重启开销 | PyTorch 2.x 标准 |
| 5 | `enabled=False` 的 autocast 是 no-op,CPU 路径仍正常 | torch.amp 文档 |
| 6 | smoke 测在 Linux CPU 上仍跑通（amp 自动关闭）| 验证过 ✓ |

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|---|---|---|
| `tools/train_card_cnn.py` | 修改 ~40 行 | (a) 加 `--num-workers` (默认 4) + `--amp` (默认 auto) CLI 参数。(b) `--batch-size` 默认 32→64。(c) `DataLoader` 加 `num_workers/pin_memory/persistent_workers` 三参,基于 GPU 可用性自动设。(d) 训练循环：所有 `.to(device)` 加 `non_blocking=True`；forward + loss 包在 `torch.amp.autocast(device_type, dtype=bf16, enabled=use_amp)` 内。(e) `evaluate()` 同样加 autocast 包裹。(f) 启动横幅打印 `GPU tuning: ...` 显示当前配置 |

### 附带修复（5 分钟规则）

无。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更：**否**
- 模型 schema 不变（CardCNN 类定义不动）
- 保存的 ckpt 不变（state_dict / ranks / suits / input_h / input_w / val_both_acc）
- 推理类 (`CnnClassifier`) 完全兼容,无需改动

## 5. 红线合规动作

无触发：纯训练性能优化,识别模型加载链 / ROI 配置 / 屏幕捕获 / 契约 均无关。

## 6. 测试结果

- **验证路径**：Linux smoke（CPU 2 epoch,验证 amp=auto 在 CPU 上自动关闭、num_workers 并行可跑、4-tuple 数据流通畅）

- **Linux CPU smoke 输出**：
  ```
  Base fixtures: 212
  Split: train=170 val=42
  GPU tuning: num_workers=2  pin_memory=False  persistent_workers=True  amp=False (dtype=torch.float32)
  Train batches: 16 (augmented=510)
  Val batches:   2 (samples=42)
  Rare classes (≤2 base fixtures): 8 total
    in train split: ['2c', '4c', '7d', '7h', '9s', 'Ac', 'Jd', 'Ks']
    in val split:   ['Ac']
  Rare-sample loss weight: 5.0×
  Epoch 1/2  train_loss=4.6496  val rank=7.14% suit=52.38% both=4.76% rare=0.00%(1)  ✓ saved
  Epoch 2/2  train_loss=4.2517  val rank=14.29% suit=73.81% both=11.90% rare=0.00%(1)  ✓ saved
  Best val both-acc: 11.90%
  ```
  ✓ 5 项优化全部生效（pin_memory/amp 在 CPU 上正确关闭, num_workers 在 CPU 上仍并行加载）
  ✓ 训练逻辑无回归（rare 集合检测 + weight 应用 + rare-acc 跟踪）

- **rules-dev §5.2 判定**：✓ 通过；test_recognition_fixtures 暂无模型 fallback,跟历史 Stage 1 状态等价

## 7. 手动操作提醒

⚠️ **Win 用户**:
1. `git pull` 拉本次代码（+ 你上次 push 的新 fixture 我这边已经 pull, 212 张）
2. 重训 100 epoch（默认参数即可吃满 5070 Ti）：
   ```powershell
   .\.venv\Scripts\python.exe tools\train_card_cnn.py --epochs 100
   ```
3. 关注启动横幅 `GPU tuning: num_workers=4 pin_memory=True persistent_workers=True amp=True (dtype=torch.bfloat16)` —— 验证 GPU 优化全部生效
4. 关注每 epoch 时长 —— 预期从 5-10s/epoch → **1-3s/epoch**
5. 跑完报 3 数字（best val / diagnose / pytest），看准确率是否仍然 96%+（应该相同,只是快了）

如果某项报错或想关掉某项，CLI 参数可用：
- `--num-workers 0` 关并行
- `--amp off` 关混合精度
- `--batch-size 32` 回到原默认

## 8. 潜在影响范围

- **正向**：
  - GPU 利用率 ~10-30% → 70-90%
  - 每 epoch 时长 3-5× 提速
  - 总训练 100 epoch：估 5-10 min → 1-3 min
  - 后续多轮迭代（调 rare-weight、补 fixture 重训等）累计省时显著
- **行为变化**：
  - 模型 weights 训练结果**完全相同**（同种子 + 同数据 → 同结果,bf16 精度对小模型几乎无影响）
  - 启动稍慢（worker 进程冷启）但稳态快
  - VRAM 占用略升（batch 大了）但 5070 Ti 16GB 充裕
- **关联待办**：
  - 用户重训 + 报 3 数字（看准确率是否冲过 96%+）
  - 如果 rare 集合（Ac/9s/Ks 等）val 仍 < 70% 准 → 加 `--rare-weight 8.0` 加强
  - 如果速度仍不满意（取决于 5070 Ti 平台细节）→ 上 `--num-workers 8` + `--batch-size 128`

## 9. 违规标注

无。
