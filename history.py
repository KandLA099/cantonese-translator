"""
history.py — SQLite 历史记录管理
================================

使用 sqlite3 标准库，不依赖外部 ORM。

数据模型:
  - sessions:   会话表 (id, created_at, title)
  - results:    识别结果表 (id, session_id, text, created_at)

支持:
  - 创建会话
  - 插入识别结果
  - 查询会话列表
  - 查询某会话的所有结果
  - 删除会话 / 清空历史
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import List, Optional, Dict, Any


class HistoryManager:
    """SQLite 历史记录管理器。

    数据库优先存储路径:
      1. Android 私有目录 (ANDROID_PRIVATE)
      2. 用户主目录 ~/.cantonese_translator/
      3. 当前工作目录
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at REAL NOT NULL,
        title TEXT
    );
    CREATE TABLE IF NOT EXISTS results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        created_at REAL NOT NULL,
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_results_session ON results(session_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created_at);
    """

    def __init__(self, db_path: Optional[str] = None):
        self._lock = threading.RLock()
        self._path = db_path or self._find_db_path()
        self._init_db()

    @staticmethod
    def _find_db_path() -> str:
        """查找最合适的数据库路径。"""
        candidates = []
        android_private = os.environ.get("ANDROID_PRIVATE")
        if android_private:
            candidates.append(os.path.join(android_private, "history.db"))
        home = os.path.expanduser("~")
        if home:
            dir_path = os.path.join(home, ".cantonese_translator")
            candidates.append(os.path.join(dir_path, "history.db"))
        candidates.append("history.db")

        for p in candidates:
            dir_name = os.path.dirname(p)
            if dir_name:
                try:
                    os.makedirs(dir_name, exist_ok=True)
                    return p
                except OSError:
                    continue
            else:
                return p
        return candidates[-1]

    def _init_db(self) -> None:
        """初始化数据库表结构。"""
        with self._lock:
            try:
                conn = sqlite3.connect(self._path, check_same_thread=False)
                conn.executescript(self._SCHEMA)
                conn.commit()
                conn.close()
            except sqlite3.Error as e:
                print(f"[History] 初始化数据库失败 ({self._path}): {e}")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, check_same_thread=False)

    def create_session(self, title: Optional[str] = None) -> int:
        """创建新会话，返回 session_id。"""
        with self._lock:
            conn = self._connect()
            cursor = conn.execute(
                "INSERT INTO sessions (created_at, title) VALUES (?, ?)",
                (time.time(), title or time.strftime("%Y-%m-%d %H:%M:%S")),
            )
            session_id = cursor.lastrowid
            conn.commit()
            conn.close()
            return session_id

    def add_result(self, session_id: int, text: str) -> bool:
        """向指定会话添加一条识别结果。"""
        with self._lock:
            try:
                conn = self._connect()
                conn.execute(
                    "INSERT INTO results (session_id, text, created_at) VALUES (?, ?, ?)",
                    (session_id, text, time.time()),
                )
                conn.commit()
                conn.close()
                return True
            except sqlite3.Error as e:
                print(f"[History] 插入结果失败: {e}")
                return False

    def get_sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取会话列表（按时间倒序）。"""
        with self._lock:
            conn = self._connect()
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT id, created_at, title FROM sessions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            rows = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return rows

    def get_session_results(self, session_id: int) -> List[Dict[str, Any]]:
        """获取某会话的所有识别结果。"""
        with self._lock:
            conn = self._connect()
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT id, text, created_at FROM results WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,),
            )
            rows = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return rows

    def delete_session(self, session_id: int) -> bool:
        """删除会话及其所有结果。"""
        with self._lock:
            try:
                conn = self._connect()
                conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
                conn.commit()
                conn.close()
                return True
            except sqlite3.Error as e:
                print(f"[History] 删除会话失败: {e}")
                return False

    def clear_all(self) -> bool:
        """清空所有历史记录。"""
        with self._lock:
            try:
                conn = self._connect()
                conn.execute("DELETE FROM results")
                conn.execute("DELETE FROM sessions")
                conn.commit()
                conn.close()
                return True
            except sqlite3.Error as e:
                print(f"[History] 清空历史失败: {e}")
                return False

    @property
    def path(self) -> str:
        return self._path


# ── 独立测试 ──────────────────────────────────────────────
if __name__ == "__main__":
    import tempfile
    db_file = os.path.join(tempfile.gettempdir(), "cantonese_test_history.db")
    if os.path.exists(db_file):
        os.remove(db_file)
    mgr = HistoryManager(db_file)
    sid = mgr.create_session("测试会话")
    print("Created session:", sid)
    mgr.add_result(sid, "你好")
    mgr.add_result(sid, "世界")
    print("Sessions:", mgr.get_sessions())
    print("Results:", mgr.get_session_results(sid))
    mgr.delete_session(sid)
    print("After delete:", mgr.get_sessions())
    os.remove(db_file)
    print("Test passed.")
