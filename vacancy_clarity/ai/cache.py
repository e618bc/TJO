"""Кэш ответов ИИ в sqlite.

Ключ включает движок, модель, версию банка вопросов и сам запрос, поэтому повторный запуск
на неизменных данных не тратит квоту и даёт тот же отчёт.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class Cache:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.execute("CREATE TABLE IF NOT EXISTS answers (key TEXT PRIMARY KEY, value TEXT, created REAL)")
        self.db.commit()

    def get(self, key: str):
        row = self.db.execute("SELECT value FROM answers WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def put(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO answers VALUES (?, ?, ?)", (key, value, time.time()))
        self.db.commit()
