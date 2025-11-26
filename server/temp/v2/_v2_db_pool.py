"""
Global PostgreSQL Connection Pool
Shared across all modules for optimal performance
"""

import asyncpg
from typing import Optional
from db_config import POSTGRES_CONFIG

# Global connection pool
_global_pool: Optional[asyncpg.Pool] = None

async def get_db_pool() -> asyncpg.Pool:
    """
    Get or create the global PostgreSQL connection pool
    
    Returns:
        asyncpg.Pool: The global database connection pool
    """
    global _global_pool
    
    if _global_pool is None:
        _global_pool = await asyncpg.create_pool(**POSTGRES_CONFIG)
        print(f"✅ PostgreSQL connection pool created ({POSTGRES_CONFIG['min_size']}-{POSTGRES_CONFIG['max_size']} connections)")
    
    return _global_pool

async def close_db_pool():
    """
    Close the global connection pool (cleanup on shutdown)
    """
    global _global_pool
    
    if _global_pool is not None:
        await _global_pool.close()
        _global_pool = None
        print("🛑 PostgreSQL connection pool closed")

