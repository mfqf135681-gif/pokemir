# T134:契约框架搭建 — invariants.md(draft)+ 契约讨论主题 + 架构纲领修正

## 1. 任务概述

2026-06-03 架构讨论收敛到"层间契约"骨架。用户授权(开发模式)新建契约讨论主题 + 搭建契约框架,并按当日共识修正 pending 的 architecture.md。借鉴 zupu `contracts/` 模式(同治理 v0.2.1),但**用途适配**(感知系统:不变量既守门又当圈梁/重建对账)。
**相邻任务**:[[砖2 守恒求解器 #226]](solver.py 将升格为 invariant-inspector);[[architecture.md]] 架构纲领(2026-06-03 建)。

## 2. 假设清单

- pokemir 已有 `contracts/{api.yaml,models.sql,views.sql}` → 缺的是 `invariants.md`(已核实)。
- `action_events.confidence_score` / `amount` / `hands.pot_size_final` 字段已存在(已读 models.sql 核实),invariants 引真实字段。
- 契约**首版强制 draft**,6 待决问题解决前不得 authoritative(审慎评估结论)。

## 3. 文件变更清单

- `contracts/invariants.md`(**新增,draft**):P 域(扑克铁律 P-1..P-8)+ U 域(UI 经验律 U-1..U-4)分本;两个 zupu 没有的维度(容差/置信 + `可纠正`/`仅标记`);R3 血案为戒。
- `requirement-discussions/主题-契约.md`(**新增,pending**):立题背景 + 已决 + 6 待决议程 + 审慎评估纪要。
- `requirement-discussions/INDEX.md`:第 9 主题"契约"登记,总览 8→9。
- `requirement-discussions/architecture.md`(修正,仍 pending):§二 金额/ID 从"花坛"纠正为"承重桩须高置信";§六 公理加 5(承重须高置信)/6(否决核心筒)/7(契约对话)/9(采集实时分析离线);§五 L1 状态改"地基未完工";§七 活跃集=L1 与契约立项标已决;头部加共识修正说明。

## 4. 契约一致性检查

- **新增 `contracts/invariants.md` = 契约变更**。本变更经用户**开发模式显式授权**(本 turn)立项;首版 draft、非 authoritative,故不构成对既有 models.sql/api.yaml 的语义冲突。晋级 active 需走 `主题-契约.md` 6 点 + 用户确认(rules-dev §6)。
- 未改 models.sql / api.yaml 字段。

## 5. 红线合规动作

- **doc-memory-hygiene**:draft/pending 状态标清,不假装完整;架构修正为蒸馏式(改既有段落非 append)。
- **mode-drift-guard**:开发模式经用户授权;契约/architecture 落地为用户明示动词("新建…搭建…修正")。
- image-only / LLM / 数据边界:不涉(纯 .md/.json 文档,L1 本地可逆)。

## 6. 测试结果

- 纯文档变更,无代码逻辑 → 无单测。轻量验证:文件创建成功、交叉引用链接指向真实文件([[主题-契约]]↔invariants.md↔architecture.md↔INDEX)。

## 7. 手动操作提醒

- 无 Win 端操作。明日继续讨论 `主题-契约.md` 6 待决问题(从①`可纠正`边界起,需用户牌手判断)。

## 8. 潜在影响范围

- 契约层是后续所有层的对话基础;invariants.md 一旦升 active,solver/重建/画像都将以它为准。当前 draft 不影响运行代码。

## 9. 提交说明

- **未 push**:契约文件不在 auto-commit 护栏白名单(rules-dev §125 path 列表无 `contracts/`)→ 护栏命中,停手问用户。本地 commit,push 待用户授权(且符合今日"commit/push 高代价必先问"边界契约)。
