#!/usr/bin/env python3
"""共享数据库初始化和工具函数。"""

import sqlite3
import os
from pathlib import Path

DB_PATH = os.environ.get(
    "ANTHROPIC_DB_PATH",
    str(Path(__file__).resolve().parent / "data" / "anthropic.db"),
)


def get_conn():
    """获取数据库连接，自动建表。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS articles (
            url TEXT PRIMARY KEY,
            title TEXT,
            date TEXT,
            category TEXT,
            summary TEXT,
            content TEXT,
            fetched_at TEXT
        );

        CREATE TABLE IF NOT EXISTS digests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            days_back INTEGER,
            article_count INTEGER,
            content TEXT,
            created_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_articles_date ON articles(date DESC);
        CREATE INDEX IF NOT EXISTS idx_digests_date ON digests(date DESC);
    """)

    return conn
