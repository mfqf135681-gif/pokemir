# T29 跨 session 玩家 alias 候选工具(不动 DB)

- **完成时间**:2026-05-28 07:35 UTC(北京 15:35)
- **关联需求讨论**:`主题-数据质量`(扩展 — 数据治理)
- **关联前次 change-log**:`2026-05-28_07-25-00_T28_pipeline_health_check.md`(地基组第 4 项)
- **触发红线**:无(只读 + 写 tools/output/ 本地 CSV)
- **无关红线已检查**:R-1 ~ R-10
- **关联 commit**:待 push

## 1. 任务概述

OCR 漂移把同一玩家拆成多个名字(豺狼I vs 豺狼I1,小雨滴笞落 vs 小雨滴答落)→ T27 净胜负 view 把同一人算两次。`tools/find_player_aliases.py` 用 difflib.SequenceMatcher 找候选合并组,**只生成报告 CSV,不动 DB**。

## 2. 假设清单

1. **假设**:difflib.SequenceMatcher ratio ≥ 0.75 是合理 alias 阈值
   - **依据**:`pipeline/orchestrator.py:611 _canonicalize_player_id_map` 同一阈值,实测 work
2. **假设**:手数多 + 字符长 = canonical(保留)
   - **现实**:规则简单可解释,**用户审核 CSV 后可手动调整**

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|:---|:---|:---|
| `tools/find_player_aliases.py` | 新建(~135 行) | difflib 相似度 + 手数排序 + CSV 输出 |

## 4. 契约一致性检查

不动 contracts/。**read-only DB**(SELECT only)+ 写 tools/output/。

## 5. 红线合规动作

无触发。**L1 操作**(本地文件写)+ **read-only DB**,无 destructive。

## 6. 测试结果

- **手动跑** `python tools/find_player_aliases.py --cutoff 0.75 --min-hands 2`:
  - 对比 50 个玩家
  - 找到 **2 对真 alias**:
    - 豺狼I → 豺狼I1(ratio 0.857,17 + 34 = 51 手应合并)
    - 小雨滴笞落 → 小雨滴答落(ratio 0.800,18 + 70 = 88 手应合并)
  - CSV 输出到 `tools/output/aliases_<TS>.csv`

## 7. 手动操作提醒

```
⚠️ 手动操作:
1. 看 CSV,人眼审核每对(可能有 false positive)
2. 确认后单独授权 UPDATE 操作:
     "我授权 UPDATE action_events SET player_name = canonical WHERE player_name = alias"
3. 将来写独立 update_player_aliases.py 工具基于 CSV 执行(L4 destructive)
4. 完成后重跑 v_player_net_winnings → 净胜负数据合并
```

## 8. 潜在影响范围

- **当前**:不动 DB,**纯查询 + 报告**
- **未来 UPDATE 后**:
  - T27 v_player_net_winnings 数据合并 → 更准
  - 玩家画像跨 session 累积
  - **不可逆**(UPDATE 后旧 alias 名字消失)

## 9. 违规标注

无违规。

---

## 任务完成自检 checklist

- ✅ `change-logs/2026-05-28_07-35-00_T29_find_player_aliases.md` 已保存
- ✅ 工具实测找到 2 对真 alias
- ✅ 无 destructive 操作(只读 + CSV 写)
- ✅ §11 模式漂移遵守(B 类授权延续)
- ✅ §1.6 校准(明示"不动 DB,审核后另立 UPDATE 工具")
