import aiosqlite
import asyncio
from typing import List, Tuple, Optional

DB_FILE = "linkplease.db"

async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS rules (
                rule_id TEXT PRIMARY KEY,
                keyword TEXT,
                dm_message TEXT
            );
            
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY
            );
            
            CREATE TABLE IF NOT EXISTS deleted_comments (
                comment_id TEXT PRIMARY KEY
            );
            
            CREATE TABLE IF NOT EXISTS dms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                rule_id TEXT,
                comment_id TEXT,
                status TEXT, -- 'queued', 'sent', 'failed'
                external_dm_id TEXT,
                UNIQUE(user_id, rule_id)
            );
            
            CREATE TABLE IF NOT EXISTS stats (
                key TEXT PRIMARY KEY,
                value INTEGER
            );
        """)
        
        # Initialize stats if not present
        await db.execute("INSERT OR IGNORE INTO stats (key, value) VALUES ('duplicates_blocked', 0)")
        await db.commit()

async def get_db():
    db = await aiosqlite.connect(DB_FILE)
    db.row_factory = aiosqlite.Row
    return db
