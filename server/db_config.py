"""
Database Configuration
Uses centralized config from config.py
"""

from config import config

# PostgreSQL Configuration (from config.py)
POSTGRES_CONFIG = {
    "host": config.DB_HOST,
    "port": getattr(config, 'DB_PORT', 5432),
    "database": config.DB_NAME,
    "user": config.DB_USER,
    "password": config.DB_PASSWORD,  # from .env (optional)
    "min_size": config.DB_MIN_POOL_SIZE,
    "max_size": config.DB_MAX_POOL_SIZE
}
