# T19 dashboard + Alias 合并 全面复检

- **诊断时间**:2026-05-28 07:50 UTC(北京 15:50)
- **状态**:**resolved**(逻辑层全过 + 1 P3 已知 limitation:UI 渲染未 Linux 真测)
- **关联需求讨论**:无(DEV 后 TEST 验证)
- **关联前次 change-log**:`2026-05-28_07-30-00_T19_position_matrix_dashboard_plus_alias_merge.md`
- **触发红线**:无
- **无关红线已检查**:R-1 ~ R-10
- **严重等级汇总**:**P0: 0 / P1: 0 / P2: 0 / P3: 1**(UI 渲染需 Win 端真测)

---

## 阶段 1:测试执行记录

### T1 Python syntax + dashboard import 链

```
✅ syntax OK
✅ profile module imported
  POSITION_ORDER: ['UTG', 'UTG+1', 'MP', 'HJ', 'CO', 'BTN', 'SB', 'BB']
  ACTION_CN keys: ['fold', 'check', 'call', 'bet', 'raise', 'all_in']
  _classify exists: True
  render exists: True
```

### T2 Alias 合并残留 verify

```sql
SELECT player_name, COUNT(*) FROM action_events
WHERE player_name IN ('豺狼I', '小雨滴笞落', '吧吧来a8') GROUP BY player_name;
→ [] (空)
```

✅ **3 个 alias 残留为 0**,UPDATE 成功且完整。

### T3 v_player_position_matrix 数据 sanity(不河沙不保 8 个 position 全有)

| 位置 | 手数 | VPIP | PFR |
|:---|:---:|:---:|:---:|
| BB | 14 | 14% | 14% |
| CO | 12 | 17% | 8% |
| BTN | 11 | 45% | **0%** ← BTN 0 PFR 不职业 |
| HJ | 11 | 45% | 36% |
| SB | 10 | 20% | 0% |
| MP | 8 | 63% | 38% |
| UTG | 7 | 43% | 0% |
| UTG+1 | 5 | 40% | 20% |

✅ 8 个 position 全覆盖 + 数字合理 + 跟手工分析一致。

### T4 v_player_net_winnings 合并后 top 5

| 玩家 | 手数 | 净胜负 |
|:---|:---:|:---:|
| 不河沙不保 | 77 | **+2129** ← 仍 top winner |
| TempUser_00000000 | 41 | +852 |
| 笑口常开运 | 24 | +705 |
| **小雨滴答落** | **93** | **+622** ← 合并后 hands +19 |
| 周润法发发 | 23 | +602 |

✅ 小雨滴答落 hands 从 74 → 93(+19 来自笞落合并),数据流转正确。

---

## 阶段 2:发现问题

### P3-建议 #1:T19 UI 渲染 Linux 无法预验证

**现象**:profile.py 是 streamlit 代码,**渲染层(布局 / 中文显示 / Plotly / dataframe)只能 Win 端真测**。
**严重度**:P3 — 跟 [[dev-rule-validate-blind-spots]] cv2 GUI 同类
**根因**:Linux VPS 无浏览器,streamlit run 即使启动也无 visual feedback
**修复路径**:用户 Win 端 git pull + reload dashboard,**截图 / 报错回报**,我修后续 commit
**当前可用性**:逻辑层(import + SQL + 数据)100% 通过,UI 大概率 OK 但**有未知 layout / 编码风险**

---

## 阶段 3:原因分析

**P3 #1 根因**:本质工程结构 — Linux dev 跟 Windows production 屏幕不在同一机器。**接受**(项目从一开始就是 image-only + 跨机器架构),依赖**用户 Win 端真测**作 final verification gate。

---

## 阶段 4:修复方案草案

**P3 #1 不需立刻修**:Win 端 git pull 后 streamlit auto-reload(`.streamlit/config.toml runOnSave=true`),用户**已经在跑 dashboard**(之前 T8 验证时 ChatGPT 帮搭),**只需要刷新页面**。

**等用户 Win 端真测后回报** → 若崩 / 报错,**新建 follow-up test-report `_open_`**。

---

## 阶段 5:全局影响评估

**T19 dashboard 改动**:
- `dashboard.py` 加 1 个 import + 1 个 PAGES entry → 不影响现有 5 个页面
- `dashboard/pages/profile.py` 新文件 → 零现有页面影响
- `contracts/views.sql` 加 1 个 view → 零现有 view 影响
- **回归测试**:用户切到其他页面("📊 复盘" / "👁 实时" / "📝 事件标注")应**仍正常**

**Alias 合并改动**:
- 49 events `player_name` 被 UPDATE → 影响 **all stats / view / dashboard 显示**
- v_player_net_winnings 已确认数字合理
- v_player_position_matrix 已确认数字合理
- **回归**:任何依赖 player_name 的查询都该刷新结果

---

## 切换提示

> ✅ T19 + alias 合并 测试逻辑层全过,**仅 UI 渲染需 Win 端真测**。
>
> 👉 你接下来:
> - "Win pull 验 UI" → git pull + streamlit 自动 reload
> - 看到崩 / 错 → 截图回报,我新开 DEV 修
> - 看正常 → 继续别的任务

---

## 任务完成自检 checklist

- ✅ `test-reports/2026-05-28_07-50-00_resolved_T19_dashboard_plus_alias_merge.md` 已保存
- ✅ `test-reports/INDEX.md` 即将同步
- ✅ 证据归档(测试命令输出 inline)
- ✅ 状态段 `_resolved_`(逻辑层全过)
- ✅ §11 模式漂移遵守(用户明示 "测试模式" → TEST)
- ✅ §1.6 校准(诚实标 UI 未真测 P3)
- ✅ Router 4 步全走
