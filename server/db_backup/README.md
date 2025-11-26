# Database Backups

This directory contains database backups for the crypto application.

## Backup Files

### crypto_db_backup.sql
- **Date**: October 18, 2025 (21:31)
- **Description**: Original backup from system PostgreSQL
- **Tokens**: 424 tokens
- **Size**: ~12MB

### crypto_db_with_424_tokens_20251018_223028.sql
- **Date**: October 18, 2025 (22:30)
- **Description**: Backup of local PostgreSQL with 424 tokens before cleanup
- **Tokens**: 424 tokens
- **Size**: ~12MB

## Restoring a Backup

To restore a backup to the local database:

```bash
# Stop the current database
/opt/homebrew/opt/postgresql@16/bin/pg_ctl -D server/db/pgdata stop

# Start the database
/opt/homebrew/opt/postgresql@16/bin/pg_ctl -D server/db/pgdata -o "-c config_file=server/db/postgresql.conf -c hba_file=server/db/pg_hba.conf" -l server/db/postgres.log start

# Restore the backup
psql -h localhost -p 5433 -U postgres -d crypto_db < server/db_backup/crypto_db_with_424_tokens_20251018_223028.sql
```

## Current Status

- **Current Database**: Empty (0 tokens)
- **Ready for**: Fresh token scanning
- **Previous Data**: Safely backed up in this directory
