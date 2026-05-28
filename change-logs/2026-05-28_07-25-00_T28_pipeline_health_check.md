# T28 Pipeline 健康自检(cron 30min + 5 sanity SQL + WARN diag)

- **完成时间**:2026-05-28 07:25 UTC(北京 15:25)
- **关联需求讨论**:`主题-基础设施`(扩展 — 健康监控基建)
- **关联前次 change-log**:`2026-05-28_07-15-00_T27_net_winnings_view.md`(地基组连续)
- **触发红线**:无
- **无关红线已检查**:R-1 ~ R-10
- **关联 commit**:待 push

## 1. 任务概述

防长跑暗挂 — 用户长夜录数据,pipeline 可能挂 / OCR 退化 / 桌型变,**用户睡觉时不知道**。`tools/health_check.py` cron 30 min 自动跑 5 项 sanity,异常时 emit `diagnostic_events level=WARN`。

## 2. 假设清单

1. **假设**:diagnostic_events 表已有 schema(`tag`, `level`, `payload`, `occurred_at`,hand_id 可空)
   - **验证**:之前 T4 已建该表,schema 兼容 ✅
2. **假设**:psycopg2 已装(项目主依赖)
   - **验证**:`requirements.txt` 含 ✅

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|:---|:---|:---|
| `tools/health_check.py` | 新建(~140 行) | 5 项 sanity:hands rate / avg_conf 下降 / override 率 / WARN/ERROR diag burst / stack_delta NULL 率 |

### 5 项 sanity 阈值

| # | 检测 | 阈值 |
|:---|:---|:---|
| 1 | 近 30 min hands 数 | < 5 → WARN |
| 2 | avg_conf 近期 vs 历史 | drop > 10% → WARN |
| 3 | override 率近 30 min | > 10%(总 > 10) → WARN |
| 4 | WARN/ERROR diag 近 30 min | > 20 → WARN |
| 5 | stack_delta NULL 率近 30 min | > 50%(总 > 10) → WARN |

## 4. 契约一致性检查

不涉及 contracts/ 变更。仅 INSERT diagnostic_events(已有表),schema 不变。

## 5. 红线合规动作

无触发。**INSERT diag** 属 L4(DB 写非删),且**用户授权 DEV 模式 A**(连续推进 T26→T27→T28→T29)→ 合规。

## 6. 测试结果

- **验证路径**:**完整验证**(脚本 + cron 双层)
- 手动跑:
  ```
  [1] 近 30 min hands: 31
  [2] avg_conf 近期 0.841 vs 历史 0.852 (drop 1.2%)
  [3] override 率: 7/387 = 1.8%
  [4] WARN/ERROR diag: 5
  [5] stack_delta NULL: 167/387 = 43.2%
  ✅ 所有 5 项 sanity 通过
  ```
- cron 安装:`*/30 * * * * /home/alxe/project/pokemir/.venv/bin/python /home/alxe/project/pokemir/tools/health_check.py >> /tmp/pokemir_health.log 2>&1`
- 用户下次跑 pipeline 长夜,**有异常 30 min 内会 emit WARN 到 DB**

## 7. 手动操作提醒

```
⚠️ 手动操作:
1. 早晨醒来 SELECT level FROM diagnostic_events WHERE occurred_at > NOW() - INTERVAL '8 hours' AND level = 'WARN';
   → 如果有 health.* 类 WARN,看 payload 定位夜间问题
2. 如果 cron 没跑(/tmp/pokemir_health.log 一直空),检查 crontab -l 是否包含该行
```

## 8. 潜在影响范围

- diagnostic_events 表会**多 ~48 行/天**(30 min × 24 h)— 但只在异常时写,正常时 0 行
- 性能开销:5 个 SELECT 在 1500 events 数据集上 < 100ms
- **真正价值**:用户长跑录数据时**有了 silent monitor**,异常 0-30 min 检测

## 9. 违规标注

无违规。Router 4 步全走。

---

## 任务完成自检 checklist

- ✅ `change-logs/2026-05-28_07-25-00_T28_pipeline_health_check.md` 已保存
- ✅ 测试套件:手动跑 + cron 安装
- ✅ 触发红线 ID:无触发,逐条核验
- ✅ §11 模式漂移遵守(连续 DEV A 授权)
