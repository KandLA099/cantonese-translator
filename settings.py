"""
settings.py — 本地配置管理
========================

使用 JSON 文件存储用户配置，支持:
  - 推理服务器地址/端口
  - VAD 灵敏度
  - 录音采样率
  - 主题偏好

所有操作线程安全（RLock）。
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, Optional


class SettingsManager:
    """本地 JSON 配置管理器。

    配置优先存储路径:
      1. Android 私有目录 (ANDROID_PRIVATE)
      2. 用户主目录 ~/.cantonese_translator/
      3. 当前工作目录
    """

    _DEFAULTS: Dict[str, Any] = {
        "inference_host": "127.0.0.1",
        "inference_port": "5001",
        "vad_mode": 1,
        "sample_rate": 16000,
        "theme": "dark",
        "auto_scroll": True,
        "enable_history": True,
    }

    def __init__(self, config_path: Optional[str] = None):
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = dict(self._DEFAULTS)
        self._path = config_path or self._find_config_path()
        self._load()

    @staticmethod
    def _find_config_path() -> str:
        """查找最合适的配置文件路径。"""
        candidates = []
        android_private = os.environ.get("ANDROID_PRIVATE")
        if android_private:
            candidates.append(os.path.join(android_private, "config.json"))
        home = os.path.expanduser("~")
        if home:
            dir_path = os.path.join(home, ".cantonese_translator")
            candidates.append(os.path.join(dir_path, "config.json"))
        candidates.append("config.json")

        for p in candidates:
            dir_name = os.path.dirname(p)
            if dir_name:
                try:
                    os.makedirs(dir_name, exist_ok=True)
                    # 测试写入权限
                    test_file = os.path.join(dir_name, ".write_test")
                    with open(test_file, "w") as f:
                        f.write("1")
                    os.remove(test_file)
                    return p
                except OSError:
                    continue
            else:
                try:
                    with open(p, "a"):
                        pass
                    return p
                except OSError:
                    continue
        return candidates[-1]

    def _load(self) -> None:
        """从磁盘加载配置。"""
        with self._lock:
            if os.path.exists(self._path):
                try:
                    with open(self._path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        self._data.update(loaded)
                except (json.JSONDecodeError, OSError, TypeError) as e:
                    print(f"[Settings] 加载配置失败 ({self._path}): {e}")

    def save(self) -> bool:
        """保存配置到磁盘。"""
        with self._lock:
            try:
                dir_name = os.path.dirname(self._path)
                if dir_name:
                    os.makedirs(dir_name, exist_ok=True)
                with open(self._path, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=2)
                return True
            except OSError as e:
                print(f"[Settings] 保存配置失败 ({self._path}): {e}")
                return False

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置项。"""
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """设置配置项（不自动保存，需显式调用 save）。"""
        with self._lock:
            self._data[key] = value

    def reset(self) -> None:
        """重置为默认值。"""
        with self._lock:
            self._data = dict(self._DEFAULTS)
            self.save()

    @property
    def path(self) -> str:
        return self._path

    def __repr__(self) -> str:
        return f"SettingsManager(path={self._path}, keys={list(self._data.keys())})"


# ── 独立测试 ──────────────────────────────────────────────
if __name__ == "__main__":
    mgr = SettingsManager()
    print("Config path:", mgr.path)
    print("Current settings:", mgr._data)
    mgr.set("test_key", "test_value")
    mgr.save()
    print("Saved.")
    mgr2 = SettingsManager(mgr.path)
    assert mgr2.get("test_key") == "test_value"
    print("Reload OK.")
