"""
utils.py — 通用工具
===================

提供:
  - AndroidUtils: 剪贴板操作（pyjnius 调用 Android ClipboardManager）
  - FileExporter: 导出文本文件到 Downloads
  - ThemeManager: 主题状态管理（辅助）
"""

from __future__ import annotations

import os
import time
from typing import Optional


def is_android() -> bool:
    if os.environ.get("ANDROID_ARGUMENT"):
        return True
    if os.environ.get("KIVY_ANDROID_LAUNCHER"):
        return True
    try:
        import platform
        if platform.system() == "Linux" and os.path.exists("/system/build.prop"):
            return True
    except Exception:
        pass
    return False


class AndroidUtils:
    """Android 平台工具（基于 pyjnius）。"""

    @staticmethod
    def copy_to_clipboard(text: str) -> bool:
        """复制文本到 Android 剪贴板。"""
        if not is_android():
            return False
        try:
            from jnius import autoclass, cast
            Context = autoclass("android.content.Context")
            ClipboardManager = autoclass("android.content.ClipboardManager")
            ClipData = autoclass("android.content.ClipData")
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            activity = PythonActivity.mActivity
            clipboard = cast(
                ClipboardManager,
                activity.getSystemService(Context.CLIPBOARD_SERVICE),
            )
            clip = ClipData.newPlainText("CantoneseTranslator", text)
            clipboard.setPrimaryClip(clip)
            return True
        except Exception as e:
            print(f"[AndroidUtils] 复制失败: {e}")
            return False

    @staticmethod
    def get_android_version() -> Optional[str]:
        """获取 Android 版本号。"""
        if not is_android():
            return None
        try:
            from jnius import autoclass
            Build = autoclass("android.os.Build")
            return Build.VERSION.RELEASE
        except Exception:
            return None

    @staticmethod
    def vibrate(milliseconds: int = 50) -> bool:
        """触发短振动。"""
        if not is_android():
            return False
        try:
            from jnius import autoclass, cast
            Context = autoclass("android.content.Context")
            Vibrator = autoclass("android.os.Vibrator")
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            activity = PythonActivity.mActivity
            vibrator = cast(
                Vibrator, activity.getSystemService(Context.VIBRATOR_SERVICE)
            )
            if vibrator and vibrator.hasVibrator():
                vibrator.vibrate(milliseconds)
                return True
        except Exception as e:
            print(f"[AndroidUtils] 振动失败: {e}")
        return False


class FileExporter:
    """文件导出工具。"""

    @staticmethod
    def export_text(
        content: str,
        filename: Optional[str] = None,
        subdir: str = "CantoneseTranslator",
    ) -> str:
        """导出文本到 Downloads 目录（Android）或当前目录（PC）。

        Args:
            content: 文件内容
            filename: 文件名（默认自动生成）
            subdir: Android 下存放的子目录名

        Returns:
            实际保存的文件路径
        """
        if filename is None:
            filename = f"export_{time.strftime('%Y%m%d_%H%M%S')}.txt"

        if is_android():
            # 优先尝试 Downloads
            paths = [
                os.path.join(os.environ.get("EXTERNAL_STORAGE", "/sdcard"), "Download", subdir),
                os.path.join("/storage/emulated/0", "Download", subdir),
                os.path.join(os.environ.get("ANDROID_PRIVATE", "."), subdir),
            ]
        else:
            paths = ["."]

        for base in paths:
            try:
                os.makedirs(base, exist_ok=True)
                full_path = os.path.join(base, filename)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)
                print(f"[FileExporter] 已导出: {full_path}")
                return full_path
            except OSError as e:
                print(f"[FileExporter] 导出失败 ({base}): {e}")
                continue

        raise RuntimeError("无法写入任何可用路径")


class ThemeManager:
    """主题状态管理（辅助类）。"""

    THEMES = {
        "dark": {
            "bg": (0.07, 0.07, 0.07, 1),
            "surface": (0.12, 0.12, 0.12, 1),
            "surface_variant": (0.17, 0.17, 0.17, 1),
            "primary": (0.3, 0.69, 0.31, 1),
            "primary_dark": (0.22, 0.56, 0.24, 1),
            "error": (0.81, 0.4, 0.48, 1),
            "text": (0.88, 0.88, 0.88, 1),
            "text_secondary": (0.63, 0.63, 0.63, 1),
            "accent": (0.01, 0.85, 0.78, 1),
            "card_bg": (0.12, 0.12, 0.12, 1),
            "divider": (0.2, 0.2, 0.2, 1),
        },
        "light": {
            "bg": (0.96, 0.96, 0.96, 1),
            "surface": (1.0, 1.0, 1.0, 1),
            "surface_variant": (0.93, 0.93, 0.93, 1),
            "primary": (0.3, 0.69, 0.31, 1),
            "primary_dark": (0.22, 0.56, 0.24, 1),
            "error": (0.69, 0.0, 0.13, 1),
            "text": (0.13, 0.13, 0.13, 1),
            "text_secondary": (0.46, 0.46, 0.46, 1),
            "accent": (0.0, 0.53, 0.53, 1),
            "card_bg": (1.0, 1.0, 1.0, 1),
            "divider": (0.88, 0.88, 0.88, 1),
        },
    }

    @classmethod
    def get_theme(cls, name: str):
        return cls.THEMES.get(name, cls.THEMES["dark"])


# ── 独立测试 ──────────────────────────────────────────────
if __name__ == "__main__":
    print("Android:", is_android())
    print("Theme dark keys:", list(ThemeManager.get_theme("dark").keys()))
    path = FileExporter.export_text("Hello World\n测试导出", filename="test_export.txt")
    print("Exported to:", path)
    if os.path.exists(path):
        os.remove(path)
    print("Test passed.")
