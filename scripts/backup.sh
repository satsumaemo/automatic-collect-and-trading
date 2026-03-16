#!/usr/bin/env bash
# ═══════════════════════════════════════
# PostgreSQL 백업 스크립트
# crontab: 0 2 * * * /path/to/scripts/backup.sh
# ═══════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="$PROJECT_DIR/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/trading_${TIMESTAMP}.sql.gz"

# 백업 디렉토리 생성
mkdir -p "$BACKUP_DIR"

# PostgreSQL 덤프 (gzip 압축)
echo "[백업] 시작 — $TIMESTAMP"
docker compose -f "$PROJECT_DIR/docker-compose.yml" exec -T postgres \
    pg_dump -U trading -d trading --no-owner --no-acl \
    | gzip > "$BACKUP_FILE"

echo "[백업] 완료 — $BACKUP_FILE"

# 30일 이전 백업 삭제
find "$BACKUP_DIR" -name "trading_*.sql.gz" -mtime +30 -delete
echo "[백업] 30일 이전 백업 정리 완료"
