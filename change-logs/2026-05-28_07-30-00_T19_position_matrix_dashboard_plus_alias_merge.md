# T19 位置维度画像 dashboard + Alias 合并

- **完成时间**:2026-05-28 07:30 UTC(北京 15:30)
- **关联需求讨论**:`主题-数据质量`(位置画像延续)
- **关联前次 change-log**:`2026-05-28_07-35-00_T29_find_player_aliases.md`(alias 工具)
- **触发红线**:无(L4 destructive UPDATE 已授权 + view 不动 schema)
- **无关红线已检查**:R-1 ~ R-10

## 1. 任务概述

用户授权 "开发模式 T19 + 我授权合并 alias",**两件事并行**:

### A. Alias 合并(L4 destructive,4 步护栏走完)

| Alias | Canonical | events |
|:---|:---|:---:|
| 豺狼I | 豺狼I1 | 23 |
| 小雨滴笞落 | 小雨滴答落 | 23 |
| 吧吧来a8 | 吧吧来aa | 3 |

合并后:豺狼I1 87 events / 51 hands;小雨滴答落 169 / 94 hands;吧吧来aa 66 / 31 hands。

### B. T19 dashboard 位置矩阵

- 新 view `v_player_position_matrix`
- 新页面 `dashboard/pages/profile.py` "👤 对手画像"
- dashboard.py PAGES dict 注册

## 2. 假设清单

1. 位置画像样本 ≥ 3 手才有意义 → dashboard 显示 caption 提示 < 5 噪声大
2. streamlit dataframe 默认支持 use_container_width=True / hide_index=True(已在 Win2 验证类似 API)
3. T27 v_player_net_winnings 列名是 `玩家`(中文),profile.py 用 `WHERE 玩家 = %s` 匹配

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|:---|:---|:---|
| `contracts/views.sql` | 修改 | 加 `v_player_position_matrix`(per player per position) |
| `dashboard/pages/profile.py` | 新建 | "👤 对手画像" 页面,140 行,含类型判定 + 矩阵 + 净胜负 + street pivot |
| `dashboard.py` | 修改 2 处 | import profile + PAGES dict 加入口 |

## 4. 契约一致性检查

- contracts/views.sql 加 view:**只读派生**,不动 table schema,**R-5/R-6 不触发**
- 已应用到 VPS PG ✅

## 5. 红线合规动作

- **L4 UPDATE alias 合并**:严格走 §5.4.4 强 keyword 授权流程
  - (a) dry-run ✅ ← 阶段 1 查了 6 个 player_name 各自 events
  - (b) 用户 keyword "我授权合并 alias" ✅
  - (c) BEGIN; UPDATE × 3; COMMIT;in transaction,执行 + SELECT 验证
  - (d) 本 change-log + T26 备份完好可恢复

## 6. 测试结果

- view 应用 ✅(`docker compose exec ... -f contracts/views.sql` CREATE VIEW)
- alias UPDATE 验证 ✅(BEGIN/COMMIT 内 + post-select 对比)
- python 语法 ✅(profile.py + dashboard.py)
- **dashboard UI 未真测** — Linux 无浏览器,Win 端 git pull + streamlit reload 验证

⚠️ [[dev-rule-validate-blind-spots]]:profile.py 大改 dashboard,可能有 streamlit 版本兼容 / 布局问题 / 中文渲染问题,Win 端真测后回报。

## 7. 手动操作提醒

```
⚠️ 手动操作:
1. Win 端(任意 dashboard 跑的机器)git pull
2. 已跑 streamlit dashboard 应自动 reload(.streamlit/config.toml runOnSave=true)
3. 看左侧 sidebar 是否多了 "👤 对手画像"
4. 点击进入,选玩家测试
5. 若崩 / 报错 → 截图 + 错误信息回报
```

## 8. 潜在影响范围

- **dashboard.py 添加 1 个 import + 1 个 PAGES entry**:不破坏其他页面
- **profile.py 新文件**:零影响现有页面
- **view 新加**:零影响现有 view / 现有 query
- **alias UPDATE 不可逆**(但 T26 备份兜底)— 49 events 改 player_name

## 9. 违规标注

无违规。Router 4 步全走:
- ✅ Mode 声明(REQ → DEV,用户明示)
- ✅ 红线核验
- ✅ §5.4 L4 destructive 4 步护栏严格走(dry-run + keyword + 验证 + change-log)
- ✅ 末尾自检

---

## 任务完成自检 checklist

- ✅ `change-logs/2026-05-28_07-30-00_T19_position_matrix_dashboard_plus_alias_merge.md` 已保存
- ✅ 测试套件:view + UPDATE + syntax check 跑通
- ✅ 触发红线 ID:无,逐条核验
- ✅ §11 模式漂移遵守(用户明示 "开发模式 T19+授权" → DEV)
- ✅ §1.6 校准:dashboard UI 未真测,已在 §6 明示风险
