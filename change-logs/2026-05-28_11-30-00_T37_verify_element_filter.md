# T37:`roi_config.py --verify` 加 `--element` 过滤 + 补全 3 漏画元素

- **完成时间**:2026-05-28 11:30 UTC(北京 19:30)
- **关联需求**:T33 9 座框选 + 用户要求"每个元素一个独立检查命令"
- **触发红线**:无
- **无关红线已检查**:R-1 ~ R-10

## 1. 任务概述

`--verify` 原本一次画全部 ROI,且**漏画 hand_type / timer / win_amount 3 个元素**。本 fix:

1. `_draw_rois` 加 `element_filter: str | None = None` 参数
2. 补全 hand_type / timer / win_amount 3 个新颜色(粉 / 青 / 浅绿)
3. 过滤模式下用 `S{idx}-{key}` 富标签,无过滤模式沿用 legacy 短标签 + 加新元素简标签
4. `--verify` 分支调用时把 `args.element` 传进去
5. 窗口标题加 `— element=X` 后缀

## 2. 假设清单

1. cv2 GUI / numpy 在 Win 端可正常 import — Linux 不能测
2. `args.element` 通过 argparse 已限定 choices=SEAT_ELEMENT_ORDER → 不会传非法 key
3. 用户每个元素跑 1 条 `--verify --element X` → 10 条命令覆盖全部 ROI

## 3. 文件变更清单

| 文件 | 变更 | 行数 |
|:---|:---|:---:|
| `tools/roi_config.py` `_draw_rois` | 重构:加参数 + 4 新颜色 + seat_elements 字典 + 过滤逻辑 | -22 / +51 |
| `tools/roi_config.py` `--verify` 分支 | 调用加 `element_filter=args.element` + 窗口标题加后缀 | +1 / -1 (内嵌)|

## 4. 契约一致性检查

- 不动 schema / pipeline / 数据流
- `_draw_rois` 旧 signature `(img, data)` → 新 signature `(img, data, element_filter=None)`,**默认参数向后兼容**所有现有调用点
- 不影响 pipeline / capture 任何代码

## 5. 红线合规动作

无触发。Router 4 步:
- ✅ Mode 声明(REQ → DEV,用户明示 "OK 走 🅰️")
- ✅ 加载文件 + AST verify
- ✅ 红线核验(全 NO)
- ✅ 自检:cv2/GUI 部分 Linux 不能真测,Win final gate

## 6. 测试结果

```
✅ syntax py_compile OK
✅ AST 验证 _draw_rois signature: (img, data, element_filter=None)
✅ AST 验证 seat_elements 含 10 个元素: ['action', 'amount', 'fold_area',
   'stack', 'cards', 'button_indicator', 'id', 'hand_type', 'timer',
   'win_amount']
⚠️ Linux 无 cv2 + GUI,真测在 Win 端
```

## 7. 手动操作提醒(Win 端)

```
1. git pull
2. python tools\roi_config.py --name party_poker_9 --verify --element action
   预期:WePoker 截图 + 仅 9 个 seat 的 action 框(橙色,标签 S0-action ...)
3. 重复 10 次,每次换 --element 值(action / amount / fold_area / stack /
   cards / button_indicator / id / hand_type / timer / win_amount)
4. 每张图肉眼核对:9 个框是否对齐对应 UI 元素
5. 不带 --element → 一次画全部(legacy 行为 + 新补 3 元素)
```

## 8. 潜在影响范围

- 仅工具行为变化,**不影响 pipeline / 数据流 / 任何 runtime 路径**
- 现有 framing 数据不变

## 9. 违规标注

无违规。

---

## 任务完成自检 checklist

- ✅ `change-logs/2026-05-28_11-30-00_T37_*.md` 已保存
- ✅ AST 验证通过
- ✅ §1.6 校准:cv2 GUI 行为 Linux 不能真测,已在 §7 明示 Win 端 final gate
- ✅ §11 模式守卫(用户明示 "OK 走 🅰️" → DEV)
