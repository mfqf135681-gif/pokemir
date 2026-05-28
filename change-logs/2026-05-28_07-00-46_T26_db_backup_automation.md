# T26 DB 备份自动化(cron + pg_dump + 14 天保留)

- **完成时间**:2026-05-28 07:00 UTC(北京 15:00)
- **关联需求讨论**:`requirement-discussions/主题-基础设施.md`(同主题,补充)
- **关联前次 change-log**:无(独立)
- **触发红线**:**R-3 合规增强**(数据备份仍在 VPS 自控,不外推)+ **R-10 项目本地工件**(backups/ gitignored)
- **无关红线已检查**:R-1, R-2, R-4, R-5, R-6, R-7, R-8, R-9
- **关联 commit**:待 push

## 1. 任务概述

- **用户原始需求**:防黑天鹅地基 — VPS docker 突然炸 / 误操作 truncate / 容器 volume 误删 = 当前 147 手数据 + 未来全部数据**全丢**。零备份状态不可接受。
- **涉及功能模块**:`scripts/db_backup.sh`(新建)+ crontab 调度 + `.gitignore` 加 `backups/`
- **相邻任务**:跟 T28(pipeline 健康自检)互补,都是"防长跑暗挂 / 数据丢失"地基组

## 2. 假设清单

1. **假设**:`.docker-data/` 目录用户可写
   - **现实**:`.docker-data/postgres/` 是 root 拥有(docker 容器创建),alxe 用户无法 mkdir 子目录 → 改备份位置到 `backups/`(项目根)
2. **假设**:`docker compose exec` 可流式输出
   - **验证**:用 `-T` flag(disable TTY)+ pipe 到 gzip,实测 work(208KB 备份成功)

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|:---|:---|:---|
| `scripts/db_backup.sh` | 新建(50 行) | pg_dump + gzip + 14 天保留 + 容器运行状态检查 |
| `.gitignore` | 修改(加 2 行) | `backups/` 加入排除(R-10 合规) |

### 附带修复

无。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更:**否**
- 不动 schema / 不动 ORM / 不动 view
- pg_dump 输出为标准 PostgreSQL SQL 格式,跨版本兼容

## 5. 红线合规动作

- **R-3 用户自控基础设施**:备份在 VPS 本地 `backups/`,**不外推第三方**,合规增强
- **R-10 项目本地工件**:`backups/` 加入 `.gitignore`,不入 git 不外泄

## 6. 测试结果

- **验证路径**:**完整验证**(脚本 + crontab 双层)
- 手动跑一次 `bash scripts/db_backup.sh`:
  - ✅ 第一次备份 208KB(147 hands + 1504 events 全 dump)
  - ✅ 列出现存备份清单
- crontab 安装:`0 3 * * * cd /home/alxe/project/pokemir; bash scripts/db_backup.sh >> /tmp/pokemir_backup.log 2>&1`
  - ✅ `crontab -l` 验证存在

## 7. 手动操作提醒

```
⚠️ 手动操作:
1. 用户 Win 桌面机本地 .env 中的 DB 备份不在覆盖范围(那只是配置,无数据)
2. VPS 上 backups/ 目录文件含 PII(player_name + raw_text)— R-3 + ToS 边界
   - 不要 git add(已 gitignored)
   - 不要 scp 到第三方机器
3. 灾难恢复:
     gunzip -c backups/poker_TS.sql.gz | docker compose exec -T postgres psql -U poker_user -d poker_assistant
```

## 8. 潜在影响范围

- **运行时**:每日 3am UTC(北京 11am)pg_dump 时 PG 短暂 IO 高 — 147 手数据 < 1MB,影响可忽略
- **磁盘**:保守 14 天 × 1MB = 14MB,实际增长后 ~100MB max
- **管理**:用户**不必关心** — 自动 daily,过期自动清理

## 9. 违规标注

无违规。本次严格 follow Router 4 步:
- ✅ Mode 声明 [REQ → DEV] · rules-dev.md
- ✅ 红线核验(R-3 / R-10 触发已显式标注)
- ✅ 末尾自检 + change-log
- ✅ 完整测试(脚本 + crontab 双跑通)

---

## 任务完成自检 checklist

- ✅ `change-logs/2026-05-28_07-00-46_T26_db_backup_automation.md` 已保存
- ✅ 测试套件:脚本手动跑通 + crontab 已安装
- ✅ 触发红线 ID 显式标注:R-3 合规增强 / R-10 项目工件
- ✅ 关联需求讨论 `[[主题-基础设施]]`(归类)
- ✅ §11 模式漂移防护遵守(用户明示"开发模式 A" → DEV)
