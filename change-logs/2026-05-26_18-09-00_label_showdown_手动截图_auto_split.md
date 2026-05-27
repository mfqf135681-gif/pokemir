# 标注 CLI 扩展:支持手动截图 + 宽图 auto-split L/R

- **完成时间**:2026-05-26 18:09
- **关联前次 change-log**:`change-logs/2026-05-26_18-04-00_pot_label_新hand-start_信号.md`
- **关联讨论**:用户决定**走自定义俱乐部 + 手动截图**路线 — 比 pipeline 自动 dump 命中率高(每手都能控制摊牌截图)

## 1. 任务概述

**问题**:用户改用自定义俱乐部 + 手动截图 workflow 后,标注 CLI 需要支持:
- 无 sibling `.json` 的纯 PNG(没有 CNN 预测元信息)
- 一张截图可能含**2 张牌**(对手 hole cards 一对) — 需要 auto-split L/R
- 默认指向 `data/showdown_manual/` 路径(与 pipeline 自动 dump 区分)
- 同时支持手动 / 自动两种来源

**改**:`tools/label_showdown.py` 重构成 per-piece(L / R / whole)迭代;`--no-split` 强制单卡模式;默认扫两个目录(dumps + manual)

## 2. 假设清单

| # | 假设 | 出处 |
|---|---|---|
| 1 | 手动截图 2 张牌 → 宽高比 > 1.4(WePoker showdown 单卡 ≈ 0.7 ratio,两张并列 ≈ 1.4)| UI 经验 |
| 2 | 无 .json 时 CLI 不卡;Enter 不可用,但 text-input 流程依然完整 | 工程设计 |
| 3 | per-piece marker(`.L.labeled` / `.R.labeled` / `.whole.labeled`)足以保证幂等 | 工程设计 |
| 4 | 兼容历史:legacy `<source>.labeled`(无 piece 后缀)存在时,所有 piece 视为已 done | 向后兼容 |

## 3. 文件变更清单

| 文件 | 变更 |
|---|---|
| `tools/label_showdown.py` | + `DEFAULT_MANUAL` 常量 + `SPLIT_ASPECT_RATIO=1.4`;改 `_iter_unlabeled` 接受 list[Path] + 返 (source, piece_id, piece_img, meta);新 `_split_pieces` aspect-ratio 判定;`_piece_marker` / `_is_piece_done` 持久化幂等;`_save_card_piece` / `_save_noncard_piece` 用 cv2.imwrite 写 piece + per-piece marker;main 改用新 API;`--no-split` 新参 |

## 4. 验证

Linux smoke test(headless,3 piece):
- ✅ wide(100×60,ratio 1.67) → L/R 切 → 标 9s/Qh,输出 `hand_001_L.png` / `hand_001_R.png` 到对应 fixture 目录
- ✅ narrow(40×60) → whole → 标 Kd,输出 `single_card_001.png`(无 _whole 后缀)
- ✅ 幂等:re-run 报 "all pieces have .labeled markers"
- ✅ per-piece 标记正确写入(`.L.labeled` / `.R.labeled` / `.whole.labeled`)

## 5. 用户后续操作

```powershell
# 1. 录自定义俱乐部 — 每手摊牌时 Win+Shift+S 截图对手牌对
#    保存到 D:\project\pokemir\data\showdown_manual\(随便命名)
# 2. 累 ~50-100 张
.venv\Scripts\python.exe tools\label_showdown.py
#    cv2 弹窗每张牌图 → 你看图 → 敲 "9s" / "Th" / "d"(noncard)
# 3. 标完跑训练
.venv\Scripts\python.exe tools\train_card_cnn.py --epochs 80
```

**截图建议**:每手只截一对手牌 + 紧贴边缘,**不要带头像 / 背景** — 提高训练信噪比。如果带了头像也没关系,标 `d` 就归入 NONCARD 负样本,**还正向贡献**。

## 6. 风险

- ⚠️ Aspect ratio 1.4 阈值:如果你截图习惯刚好接近这个比例 → 可能误切。`--no-split` 强制单卡模式兜底
- ⚠️ cv2.imshow 在 PowerShell 启动的 Python 进程是否能弹窗:理论可,Win 桌面默认能 — 实测如果不弹,用 `--no-display`(blind label)兜底

## 7. 不在 scope

- ❌ 截图工具/快捷键 — Win+Shift+S 已经够用
- ❌ 自动切多张牌(超过 2 张的 community)— 本次只处理 1-2 张

## 关联记忆

- 流程演进,无需更新记忆
