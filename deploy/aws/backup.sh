#!/usr/bin/env bash
# Dump the database to s3://$BACKUP_BUCKET/ic/ (uses the instance role). Run nightly from cron:
#   15 3 * * * /home/ubuntu/InsightfullCell/deploy/aws/backup.sh >> /home/ubuntu/ic-backup.log 2>&1
# Restore: README.md, "Restoring a backup".
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
set -a; source .env; set +a
: "${BACKUP_BUCKET:?set BACKUP_BUCKET in deploy/aws/.env}"

key="ic/insightfulcell-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose exec -T postgres pg_dump -U ic_owner -d insightfulcell -Fc \
  | aws s3 cp - "s3://$BACKUP_BUCKET/$key"
echo "$(date -u +%FT%TZ) backed up to s3://$BACKUP_BUCKET/$key"
