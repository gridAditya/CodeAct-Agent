import asyncpg

from colorama import Fore, Style
from config import logging_config
import logging

async def create_pool(dsn: str, *, min_size: int = 1, max_size: int = 10) -> asyncpg.Pool:
    """
    Create and return an asyncpg pool.
    Caller is responsible for closing the pool (await pool.close()).
    """
    pool = await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size)
    return pool

async def close_pool(pool: asyncpg.Pool) -> None:
    await pool.close()