"""
V3 Database Pool for PostgreSQL (crypto.db)
Uses PostgreSQL with new crypto.db database
"""

import asyncpg
from typing import Optional
from db_config import POSTGRES_CONFIG
from _v3_db_init import init_database

_global_pool: Optional[asyncpg.Pool] = None

async def get_db_pool() -> asyncpg.Pool:
    global _global_pool
    
    if _global_pool is None:
        await init_database()
        
        config = POSTGRES_CONFIG.copy()
        config['database'] = 'crypto_db'

        _global_pool = await asyncpg.create_pool(**config)
    
    return _global_pool

async def close_db_pool():
    global _global_pool
    
    if _global_pool is not None:
        await _global_pool.close()
        
        _global_pool = None
