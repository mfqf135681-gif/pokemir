#!/usr/bin/env bash
# T26: pokemir DB 自动备份(2026-05-28 立)
#
# 防黑天鹅:VPS docker 炸 / 误操作 truncate / 等灾难场景下数据可恢复。
#
# 用法:
#   手动单次: bash scripts/db_backup.sh
#   自动 daily: crontab -e 加一行
#     0 3 * * * cd /home/alxe/project/pokemir && bash scripts/db_backup.sh >> /tmp/pokemir_backup.log 2>&1
#
# 备份位置: backups/poker_<UTC_TS>.sql.gz
#   - backups/ 已 gitignored(R-10 合规,见 .gitignore)
#   - 仍在 VPS 本地(R-3 合规,数据未外推)
#
# 保留策略: 14 天(自动 mtime-based 清理)

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="$PROJECT_ROOT/backups"
mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date -u +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/poker_${TIMESTAMP}.sql.gz"

cd "$PROJECT_ROOT"

# 验 docker compose 在线
if ! docker compose ps --status running 2>/dev/null | grep -q postgres; then
  echo "❌ postgres 容器未运行,跳过备份"
  exit 1
fi

# pg_dump via docker exec,gzip on the fly
docker compose exec -T postgres \
  pg_dump -U poker_user -d poker_assistant \
  | gzip > "$BACKUP_FILE"

SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "✅ Backup: $BACKUP_FILE ($SIZE)"

# 保留最近 14 天
DELETED=$(find "$BACKUP_DIR" -name "poker_*.sql.gz" -mtime +14 -print -delete | wc -l)
if [ "$DELETED" -gt 0 ]; then
  echo "🗑️  清理过期备份: $DELETED 个"
fi

# 当前备份清单
echo ""
echo "📋 现存备份(最近 5 个):"
ls -lh "$BACKUP_DIR"/poker_*.sql.gz 2>/dev/null | tail -5 || echo "  (无)"
