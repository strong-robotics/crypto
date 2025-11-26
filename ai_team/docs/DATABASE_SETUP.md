# Database Setup Guide

## Local PostgreSQL Database

This project uses a local PostgreSQL database stored in `server/db/pgdata/` directory. This allows the entire project (including database) to be easily shared via GitHub.

### Database Configuration

- **Host**: localhost
- **Port**: 5433
- **Database**: crypto_db
- **User**: postgres
- **Password**: (none)

### Starting the Database

The database is automatically started when you run `./start.sh`. If you need to start only the database:

```bash
./start-postgres.sh
```

### Manual Database Management

#### Start PostgreSQL:
```bash
/opt/homebrew/opt/postgresql@16/bin/pg_ctl -D server/db/pgdata -o "-c config_file=server/db/postgresql.conf -c hba_file=server/db/pg_hba.conf" -l server/db/postgres.log start
```

#### Stop PostgreSQL:
```bash
/opt/homebrew/opt/postgresql@16/bin/pg_ctl -D server/db/pgdata stop
```

#### Connect to Database:
```bash
psql -h localhost -p 5433 -U postgres -d crypto_db
```

### Database Files

- `server/db/pgdata/` - PostgreSQL data directory (included in git)
- `server/db/postgresql.conf` - PostgreSQL configuration
- `server/db/pg_hba.conf` - Authentication configuration
- `server/db/postgres.log` - Database logs (ignored by git)
- `server/db_backup/` - Database backups

### Sharing the Project

The entire project including the database can be shared via GitHub. The database files are stored in `server/db/pgdata/` and are included in the repository.

### Requirements

- PostgreSQL 16+ (installed via Homebrew)
- Path: `/opt/homebrew/opt/postgresql@16/bin/`

### Troubleshooting

If you encounter issues:

1. Check if PostgreSQL is running: `pgrep -f "postgres.*server/db/pgdata"`
2. Check logs: `cat server/db/postgres.log`
3. Restart database: `./start-postgres.sh`
4. Check port availability: `lsof -i :5433`
