# T34 + T35:INDEX.md 补 T26-T29 entry + 9 座 paste v2(基于最新 8 座)

- **完成时间**:2026-05-28 10:45 UTC(北京 18:45)
- **关联 test-report**:`test-reports/2026-05-28_10-30-00_resolved_T33_GPT合并冲突丢条目.md`
- **关联前次 change-log**:`2026-05-28_09-00-00_T33_9seat_profile_paste.md`(被本 fix 覆盖)
- **触发红线**:无
- **无关红线已检查**:R-1 ~ R-10

## 1. 任务概述

### T34:补 INDEX.md 丢失 entry

GPT 在 commit 4f8f2a9 合并冲突时,把 T26-T29 entry 替换成 Dashboard entry 而非追加。本 fix 补回 T26-T29 链接。

### T35:9 座 paste v2

原 49b591a paste 用的是过期 8 座几何(commit 610f41f),缺:
- 全座 win_amount
- 全座统一 124×27 id
- hero seat_0 timer
- 全座收窄 action(40-44px)

重做 paste,seat 0/1/2/3/6/7/8 从 4f8f2a9 最新 8 座继承全部字段。seat_4/5 仍留 `_TODO` 占位符。

## 2. 假设清单

1. T34:删除的 entry 直接 add 回原位 line 12 → 与 T19 entry 之后 / Dashboard entry 之前(时序对齐)
2. T35:Win 端最新 8 座几何在 9 座 0/1/2/3/6/7/8 同位置仍可复用(待 Win --verify 真测)

## 3. 文件变更清单

| 文件 | 变更 | 说明 |
|:---|:---|:---|
| `test-reports/INDEX.md` | +1 entry(T26-T29)+ Open→Resolved 转移本报告 | T34 + 状态更新 |
| `rois/party_poker_9.json` | seat 0/1/2/3/6/7/8 全字段更新 | T35,基于 4f8f2a9 8 座 |
| `test-reports/2026-05-28_10-30-00_open_T33_*.md` | 文件名 `_open_` → `_resolved_` | 状态转移 |

## 4. 契约一致性检查

- 不动 schema/contracts
- 不动 ORM model
- 9 座 paste 字段集与 8 座一致(action/amount/fold_area/button_indicator/id/cards/hand_type/timer/win_amount/stack)

## 5. 红线合规动作

无触发。Router 4 步:
- ✅ Mode 声明(REQ → DEV,用户明示 "OK 走 🅰️")
- ✅ 加载文件(读 8 座 latest + 9 座 v1 + test-reports/INDEX.md)
- ✅ 红线核验(R-1~R-10 全 NO)
- ✅ 自检

## 6. 测试结果

### T35 9 座 paste v2 字段完整性

```
seat_0: OK | action_w=40 id_wh=124x27 win_amount_w=113
seat_1: OK | action_w=44 id_wh=124x27 win_amount_w=113
seat_2: OK | action_w=42 id_wh=124x27 win_amount_w=113
seat_3: OK | action_w=43 id_wh=124x27 win_amount_w=113
seat_4: TODO 占位符
seat_5: TODO 占位符
seat_6: OK | action_w=41 id_wh=124x27 win_amount_w=113
seat_7: OK | action_w=44 id_wh=124x27 win_amount_w=113
seat_8: OK | action_w=40 id_wh=124x27 win_amount_w=113
```

✅ 7 个 seat 全部继承最新 8 座几何 + 完整 10 字段。

### T34 INDEX.md 完整性

```
## 🟢 Resolved (4 entries)
- T17+T18 P1 已修
- T19 dashboard + Alias
- T26-T29 地基组   ← T34 补回
- Dashboard 独立环境启动验证
- T33 GPT 合并冲突 (本次 fix)
```

(实际 5 entries — 加了本次 fix 自身)

## 7. 手动操作提醒(Win 端)

```
⚠️ Win 端:
1. git pull(拉到 T34 + T35 v2)
2. python tools\roi_config.py --name party_poker_9 --verify
   → 应看到 7 个 seat 框完整叠加显示(含 win_amount / timer 等所有 sub-ROI)
3. 如几何对齐 → 框 seat_4 + seat_5
   python tools\roi_config.py --name party_poker_9 --seats 9 --field seat_4 --window "WePoker"
   python tools\roi_config.py --name party_poker_9 --seats 9 --field seat_5 --window "WePoker"
4. 录 1 手 9 座观战真测落库
```

## 8. 潜在影响范围

- 9 座 paste v2 完全替代 v1
- 不影响 8 座 profile
- 不影响 pipeline 代码
- INDEX.md 仅文档,不影响 runtime

## 9. 违规标注

无违规。

## 10. 教训沉淀(本次最大收获)

GPT 在合并冲突时**形式上 100% 遵循 Router 4 步 + 红线核验 + 必报 4 项**,但**实质上漏读了我模板里的 1 条 entry**。

这印证了 `[[dev-rule-validate-blind-spots]]` 的延伸价值:

> **协议遵循度 ≠ 内容正确性**。即使 AI 协议 100% 合规,**TEST 复检不可省**。

考虑后续把这个教训沉到 memory(待用户决定是否新立 dev-rule)。

---

## 任务完成自检 checklist

- ✅ `change-logs/2026-05-28_10-45-00_T34_T35_*.md` 已保存
- ✅ T34 + T35 syntax + 内容验证通过
- ✅ test-report 状态 _open_ → _resolved_
- ✅ INDEX.md 同步更新
- ✅ §1.6 校准:9 座 paste v2 几何 Linux 无法真测,**Win --verify 才是 final gate**
- ✅ §11 模式守卫(用户明示 "OK 走 🅰️" → DEV 授权)
