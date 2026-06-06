# created_at 默认值冻结为常量 — 根因 + 修复(#210)

## 1. 概述

`hands.created_at` 和 `action_events.created_at` 的列默认值在 DB 里是**冻死的常量
时间戳** `'2026-06-06 14:13:32.263633+08'`(= DB 迁移重建那一刻),不是 `now()`。
**不丢数据**(`started_at`/`timestamp` Python 端写,正确),但**毒化一切按 created_at
的时间查询**——本次排查我自己就被坑:误判"管线没在写库",实际 148 手 / 20 分钟
145 动作一直在 live 写。

## 2. 根因(石锤)

- `storage/models.py:33,60`:`Column(..., server_default="now()")` —— 传**裸字符串**
  `"now()"`(规范写法是 `text("now()")` / `func.now()`)。
- `init_db()` = `Base.metadata.create_all`(走模型),迁移重建时把这个烤成了死字面量。
- **铁证级相关**:全库 `information_schema` 里**唯独这两列**有冻结默认值,且**正是仅有的
  两处 `server_default="now()"`**;其余列(`started_at`/`occurred_at`)无此问题。
- **意图佐证**:`contracts/models.sql:36,62` 写的是 `DEFAULT now()`(正确 live)——证明
  设计意图是 live,模型那行字符串与参考 SQL 不一致才是 bug。
- ⚠️ 未在 Linux 复现 DDL 渲染机制(本机无 SQLAlchemy);结论靠 DB 观测 + 完美相关 +
  参考 SQL 三方交叉,非凭印象。

## 3. 修复

### 已做(代码,根治未来重建)
`storage/models.py`:两处 `server_default="now()"` → `server_default=text("now()")`
(`text` 已 import)。今后 `create_all` 渲染为 live `DEFAULT now()`,与 contracts 一致。

### 待做(Win 端,修活库现有列;用户执行)
现有库的列默认还是死值,改它(只改未来插入的默认,**不动任何已存行、不丢数据**):
```
psql -U poker_user -d poker_assistant -h 127.0.0.1 -c "ALTER TABLE hands ALTER COLUMN created_at SET DEFAULT now(); ALTER TABLE action_events ALTER COLUMN created_at SET DEFAULT now();"
```
跑完验证:`SELECT column_default FROM information_schema.columns WHERE table_name='hands' AND column_name='created_at';` 应显示 `now()` 而非 2026 字面量。

## 4. 影响面 / 严限

- 历史行的 created_at 已冻死无法追回——但**它本就是审计列**,真实时间在
  `started_at`/`timestamp`/`occurred_at`,分析一律用那些列(别用 created_at 过滤)。
- 仅 1 次观测会话(本次 live);ALTER 后需下次 live 验证新行 created_at 走 now()。
- contracts/models.sql 已正确,无需改(且不在 auto-commit 名单)。

## 5. 关联

#210(T118 created_at 默认值 bug)——本次定位 + 根因 + 代码修;Win ALTER 待用户跑。
排查副产:确认 live amount 捕获正常(call 100% / bet 92% / raise 86% 有金额),
准度评估(对标 95%)仍需真值,另起。
