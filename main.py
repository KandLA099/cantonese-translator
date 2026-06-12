#!/usr/bin/env python3
"""
main.py — 粤语实时翻译 Android 主程序 (Kivy SDL2)
=================================================

修复与优化:
  - 所有非标准库 import 延迟到使用时，try/except 包裹
  - 移除 kivy.require() 严格版本检查
  - PC 环境可启动（模拟模式，UI 正常）
  - 修复 _show_error 与空状态冲突
  - 修复 auto-scroll 死代码
  - 修复 Window.width 旋转问题
  - _loop() 线程安全（加锁访问 capture.is_running）
  - 现代暗色主题 + 卡片式结果 + 动画
  - 设置面板 / 历史记录 / 复制 / 导出 / 主题切换 / 错误重连
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
import threading
import time
import traceback
import urllib.request
from typing import Optional, List, Dict, Any

# ── Kivy 环境变量（必须在 kivy import 前）─────────────────
os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_NO_CONSOLELOG", "0")

# ── 崩溃日志捕获 ──────────────────────────────────────────
def _get_log_paths():
    """返回所有可能的日志路径（优先用户可见目录）。"""
    pkg = "com.cantonesetranslator"
    candidates = [
        # 用户一定可见的目录（Android 10+ 仍允许应用写 Download）
        f"/storage/emulated/0/Download/{pkg}",
        "/sdcard/Download",
        # 应用外部目录
        os.environ.get("EXTERNAL_STORAGE"),
        f"/storage/emulated/0/Android/data/{pkg}/files",
        os.environ.get("ANDROID_PRIVATE"),
        # fallback
        os.environ.get("HOME"),
        os.path.expanduser("~"),
        "/data/local/tmp",
        "/sdcard",
        ".",
    ]
    return [p for p in candidates if p]

def _try_write_log(path: str, text: str) -> bool:
    if not path:
        return False
    try:
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)
        return True
    except Exception:
        return False

def _write_crash_log(exc_type, exc_value, exc_tb):
    tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    print(f"\n!!!!! CRASH {exc_type.__name__}: {exc_value}", flush=True)
    for line in tb_text.splitlines():
        print(f"[CRASH] {line}", flush=True)
    text = f"\n{'='*60}\nCRASH at {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
    text += f"[PID {os.getpid()}] {exc_type.__name__}: {exc_value}\n{'='*60}\n"
    text += tb_text + "\n"
    for p in _get_log_paths():
        if p:
            _try_write_log(os.path.join(p, "cantonese_crash.log"), text)

_MARKERS_WRITTEN = 0
for _p in _get_log_paths():
    if _try_write_log(os.path.join(_p, "cantonese_started.txt"),
                      f"STARTED at {time.strftime('%Y-%m-%d %H:%M:%S')}\n"):
        _MARKERS_WRITTEN += 1

sys.excepthook = _write_crash_log

# ── 日志 ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

for _p in _get_log_paths():
    try:
        _fh = logging.FileHandler(
            os.path.join(_p, "cantonese_crash.log"), encoding="utf-8"
        )
        _fh.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logging.getLogger().addHandler(_fh)
        break
    except Exception:
        continue

# ── Kivy imports ──────────────────────────────────────────
from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle, RoundedRectangle
from kivy.metrics import dp
from kivy.properties import NumericProperty, StringProperty, BooleanProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.slider import Slider
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.animation import Animation
from kivy.utils import get_color_from_hex

# ── 配置 ──────────────────────────────────────────────────
INFERENCE_HOST = os.environ.get("INFERENCE_HOST", "127.0.0.1")
INFERENCE_PORT = os.environ.get("INFERENCE_PORT", "5001")
INFERENCE_URL = f"http://{INFERENCE_HOST}:{INFERENCE_PORT}"

# ── 颜色主题 ──────────────────────────────────────────────
THEMES: Dict[str, Dict[str, Any]] = {
    "dark": {
        "bg": get_color_from_hex("#121212"),
        "surface": get_color_from_hex("#1E1E1E"),
        "surface_variant": get_color_from_hex("#2C2C2C"),
        "primary": get_color_from_hex("#4CAF50"),
        "primary_dark": get_color_from_hex("#388E3C"),
        "error": get_color_from_hex("#CF6679"),
        "text": get_color_from_hex("#E0E0E0"),
        "text_secondary": get_color_from_hex("#A0A0A0"),
        "accent": get_color_from_hex("#03DAC6"),
        "card_bg": get_color_from_hex("#1E1E1E"),
        "divider": get_color_from_hex("#333333"),
    },
    "light": {
        "bg": get_color_from_hex("#F5F5F5"),
        "surface": get_color_from_hex("#FFFFFF"),
        "surface_variant": get_color_from_hex("#EEEEEE"),
        "primary": get_color_from_hex("#4CAF50"),
        "primary_dark": get_color_from_hex("#388E3C"),
        "error": get_color_from_hex("#B00020"),
        "text": get_color_from_hex("#212121"),
        "text_secondary": get_color_from_hex("#757575"),
        "accent": get_color_from_hex("#018786"),
        "card_bg": get_color_from_hex("#FFFFFF"),
        "divider": get_color_from_hex("#E0E0E0"),
    },
}

# ── 延迟加载模块（避免启动崩溃）─────────────────────────────
_AndroidAudioCapture = None
_AudioProcessor = None
_numpy = None
_android_utils = None


def _lazy_load_modules():
    """延迟加载所有可能崩溃的第三方模块。"""
    global _AndroidAudioCapture, _AudioProcessor, _numpy, _android_utils

    if _AndroidAudioCapture is None:
        try:
            from android_audio import AndroidAudioCapture
            _AndroidAudioCapture = AndroidAudioCapture
            logger.info("AndroidAudioCapture 已加载")
        except Exception as e:
            logger.warning("AndroidAudioCapture 加载失败: %s", e)
            _AndroidAudioCapture = False

    if _AudioProcessor is None:
        try:
            from audio_processor import AudioProcessor
            _AudioProcessor = AudioProcessor
            logger.info("AudioProcessor 已加载")
        except Exception as e:
            logger.warning("AudioProcessor 加载失败: %s", e)
            _AudioProcessor = False

    if _numpy is None:
        try:
            import numpy as np
            _numpy = np
            logger.info("numpy 已加载")
        except Exception as e:
            logger.warning("numpy 加载失败: %s", e)
            _numpy = False

    if _android_utils is None:
        try:
            from utils import AndroidUtils
            _android_utils = AndroidUtils
            logger.info("AndroidUtils 已加载")
        except Exception as e:
            logger.warning("AndroidUtils 加载失败: %s", e)
            _android_utils = False


# ── 工具：检测 Android 环境 ────────────────────────────────
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


# ── 小部件：电平条 ────────────────────────────────────────
class LevelBar(Widget):
    """音频电平指示条（竖条，带颜色渐变）。"""
    level = NumericProperty(0.0)

    def __init__(self, theme: Dict[str, Any], **kwargs):
        super().__init__(**kwargs)
        self.theme = theme
        self.bind(pos=self._redraw, size=self._redraw, level=self._redraw)

    def _redraw(self, *args):
        self.canvas.clear()
        with self.canvas:
            Color(*self.theme["divider"])
            RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(4)] * 4)
            h = self.height * min(self.level / 100.0, 1.0)
            if h > 0:
                if self.level > 80:
                    Color(*self.theme["error"])
                elif self.level > 50:
                    Color(1.0, 0.76, 0.03, 1.0)
                else:
                    Color(*self.theme["primary"])
                RoundedRectangle(
                    pos=(self.x, self.y),
                    size=(self.width, h),
                    radius=[dp(4)] * 4,
                )


# ── 小部件：状态呼吸灯 ────────────────────────────────────
class StatusDot(Widget):
    """圆形状态指示灯，支持呼吸动画。"""
    is_active = BooleanProperty(False)

    def __init__(self, theme: Dict[str, Any], **kwargs):
        super().__init__(**kwargs)
        self.theme = theme
        self._anim = None
        self.bind(pos=self._draw, size=self._draw, is_active=self._on_active)
        Clock.schedule_once(self._draw, 0)

    def _draw(self, *args):
        self.canvas.clear()
        with self.canvas:
            if self.is_active:
                Color(*self.theme["primary"])
            else:
                Color(*self.theme["text_secondary"])
            RoundedRectangle(
                pos=(self.center_x - self.width / 2, self.center_y - self.height / 2),
                size=(self.width, self.height),
                radius=[self.width] * 4,
            )

    def _on_active(self, *args):
        self._draw()
        if self._anim:
            self._anim.cancel(self)
        if self.is_active:
            self._anim = Animation(opacity=0.4, duration=0.8) + Animation(
                opacity=1.0, duration=0.8
            )
            self._anim.repeat = True
            self._anim.start(self)
        else:
            self.opacity = 1.0


# ── 小部件：结果卡片 ──────────────────────────────────────
class ResultCard(BoxLayout):
    """单条识别结果卡片。"""

    def __init__(self, text: str, timestamp: str, theme: Dict[str, Any], **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.size_hint_y = None
        self.padding = [dp(12), dp(8)]
        self.spacing = dp(4)
        self.theme = theme

        ts_lbl = Label(
            text=timestamp,
            color=theme["text_secondary"],
            font_size=dp(11),
            size_hint_y=None,
            height=dp(16),
            halign="left",
            text_size=(None, None),
        )
        ts_lbl.bind(texture_size=lambda *x: ts_lbl.setter("width")(ts_lbl, ts_lbl.texture_size[0]))

        text_lbl = Label(
            text=text,
            color=theme["text"],
            font_size=dp(16),
            halign="left",
            valign="top",
            size_hint_y=None,
            text_size=(Window.width - dp(56), None),
        )
        text_lbl._is_result_text = True
        text_lbl.bind(
            texture_size=lambda *x: setattr(
                text_lbl, "height", text_lbl.texture_size[1] + dp(8)
            )
        )

        self.add_widget(ts_lbl)
        self.add_widget(text_lbl)
        self.bind(
            minimum_height=lambda *x: setattr(
                self, "height", self.minimum_height + dp(16)
            )
        )

        # 入场动画
        self.opacity = 0
        self.x += dp(20)
        Clock.schedule_once(lambda dt: self._animate_in(), 0.05)

        # 长按复制（简单实现：双击复制当前文本）
        self._text_to_copy = text
        self._touch_start_time = 0.0

    def _animate_in(self):
        anim = Animation(opacity=1, x=self.x - dp(20), duration=0.3, t="out_quad")
        anim.start(self)

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            self._touch_start_time = time.time()
        return super().on_touch_down(touch)

    def on_touch_up(self, touch):
        if self.collide_point(*touch.pos):
            duration = time.time() - self._touch_start_time
            if duration > 0.5:
                self._copy_text()
        return super().on_touch_up(touch)

    def _copy_text(self):
        try:
            if _android_utils:
                _android_utils.copy_to_clipboard(self._text_to_copy)
                self._show_toast("已复制到剪贴板")
            else:
                import pyperclip
                pyperclip.copy(self._text_to_copy)
                self._show_toast("已复制")
        except Exception as e:
            logger.warning("复制失败: %s", e)
            self._show_toast("复制失败")

    def _show_toast(self, msg: str):
        toast = Label(
            text=msg,
            color=self.theme["accent"],
            font_size=dp(12),
            size_hint=(None, None),
            size=(dp(120), dp(24)),
            pos=(self.center_x - dp(60), self.y + dp(8)),
        )
        if self.parent:
            self.parent.add_widget(toast)
            anim = (
                Animation(opacity=1, duration=0.2)
                + Animation(opacity=1, duration=1.5)
                + Animation(opacity=0, duration=0.3)
            )
            anim.bind(on_complete=lambda *a: self.parent.remove_widget(toast) if toast in self.parent.children else None)
            anim.start(toast)


# ── 设置面板（Popup）───────────────────────────────────────
class SettingsPopup(Popup):
    def __init__(self, app: "TranslatorApp", **kwargs):
        self.app = app
        self.theme = app.theme
        super().__init__(**kwargs)
        self.title = "设置"
        self.title_color = self.theme["text"]
        self.title_size = dp(18)
        self.background_color = self.theme["surface"]
        self.size_hint = (0.9, 0.7)
        self.auto_dismiss = True

        content = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12))

        # 服务器地址
        content.add_widget(self._make_label("推理服务器地址"))
        self.host_input = TextInput(
            text=app.settings.get("inference_host", INFERENCE_HOST),
            multiline=False,
            background_color=self.theme["surface_variant"],
            foreground_color=self.theme["text"],
            cursor_color=self.theme["primary"],
            font_size=dp(14),
            size_hint_y=None,
            height=dp(40),
        )
        content.add_widget(self.host_input)

        content.add_widget(self._make_label("端口"))
        self.port_input = TextInput(
            text=str(app.settings.get("inference_port", INFERENCE_PORT)),
            multiline=False,
            input_filter="int",
            background_color=self.theme["surface_variant"],
            foreground_color=self.theme["text"],
            cursor_color=self.theme["primary"],
            font_size=dp(14),
            size_hint_y=None,
            height=dp(40),
        )
        content.add_widget(self.port_input)

        # VAD 灵敏度
        content.add_widget(self._make_label("VAD 灵敏度 (0-3)"))
        vad_box = BoxLayout(size_hint_y=None, height=dp(40))
        self.vad_slider = Slider(
            min=0, max=3, value=app.settings.get("vad_mode", 1),
            size_hint_x=0.8,
        )
        self.vad_value = Label(
            text=str(int(self.vad_slider.value)),
            color=self.theme["text"],
            size_hint_x=0.2,
        )
        self.vad_slider.bind(
            value=lambda *a: self.vad_value.setter("text")(
                self.vad_value, str(int(self.vad_slider.value))
            )
        )
        vad_box.add_widget(self.vad_slider)
        vad_box.add_widget(self.vad_value)
        content.add_widget(vad_box)

        # 采样率
        content.add_widget(self._make_label("录音采样率 (Hz)"))
        self.sr_input = TextInput(
            text=str(app.settings.get("sample_rate", 16000)),
            multiline=False,
            input_filter="int",
            background_color=self.theme["surface_variant"],
            foreground_color=self.theme["text"],
            cursor_color=self.theme["primary"],
            font_size=dp(14),
            size_hint_y=None,
            height=dp(40),
        )
        content.add_widget(self.sr_input)

        # 主题切换
        content.add_widget(self._make_label("主题"))
        theme_box = BoxLayout(size_hint_y=None, height=dp(40))
        self.btn_dark = Button(
            text="暗色",
            background_color=self.theme["primary"] if app.theme_name == "dark" else self.theme["surface_variant"],
            color=self.theme["text"],
        )
        self.btn_light = Button(
            text="亮色",
            background_color=self.theme["primary"] if app.theme_name == "light" else self.theme["surface_variant"],
            color=self.theme["text"],
        )
        self.btn_dark.bind(on_press=lambda *a: self._set_theme("dark"))
        self.btn_light.bind(on_press=lambda *a: self._set_theme("light"))
        theme_box.add_widget(self.btn_dark)
        theme_box.add_widget(self.btn_light)
        content.add_widget(theme_box)

        # 保存按钮
        btn_save = Button(
            text="保存",
            background_color=self.theme["primary"],
            color=self.theme["text"],
            size_hint_y=None,
            height=dp(48),
        )
        btn_save.bind(on_press=self._save)
        content.add_widget(btn_save)

        self.content = content

    def _make_label(self, text: str) -> Label:
        return Label(
            text=text,
            color=self.theme["text_secondary"],
            font_size=dp(13),
            size_hint_y=None,
            height=dp(24),
            halign="left",
            text_size=(None, None),
        )

    def _set_theme(self, name: str):
        self.app.theme_name = name
        self.btn_dark.background_color = (
            self.theme["primary"] if name == "dark" else self.theme["surface_variant"]
        )
        self.btn_light.background_color = (
            self.theme["primary"] if name == "light" else self.theme["surface_variant"]
        )

    def _save(self, *args):
        self.app.settings.set("inference_host", self.host_input.text.strip())
        self.app.settings.set("inference_port", self.port_input.text.strip())
        self.app.settings.set("vad_mode", int(self.vad_slider.value))
        self.app.settings.set("sample_rate", int(self.sr_input.text.strip() or 16000))
        self.app.settings.set("theme", self.app.theme_name)
        self.app.settings.save()
        self.app._update_inference_url()
        self.app._apply_theme()
        self.dismiss()
        self.app.ui._show_toast("设置已保存")


# ── 主 UI ─────────────────────────────────────────────────
class TranslatorUI(BoxLayout):
    def __init__(self, app: "TranslatorApp", **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self.theme = app.theme
        self.orientation = "vertical"
        self.padding = 0
        self.spacing = 0

        # 状态
        self.is_recording = False
        self.start_time = 0.0
        self.last_text = ""
        self.results_count = 0
        self._session_id: Optional[int] = None

        # 音频
        self.capture = None
        self.processor = None
        self._stop_event = threading.Event()
        self._processing_thread: Optional[threading.Thread] = None
        self._timer_event = None
        self._timer_event2 = None
        self._capture_lock = threading.Lock()

        # 错误重连
        self._error_count = 0
        self._max_retries = 3

        self._build_ui()
        Window.bind(on_resize=self._on_resize)
        self._resize_bound = True

    def _build_ui(self):
        self.clear_widgets()

        # ─── 顶栏 ───────────────────────────────
        top = BoxLayout(
            size_hint_y=None, height=dp(56), spacing=dp(8), padding=[dp(12), dp(8)]
        )
        top.bind(pos=self._draw_top, size=self._draw_top)

        self.status_dot = StatusDot(
            theme=self.theme, size_hint=(None, None), size=(dp(12), dp(12))
        )
        self.st_label = Label(
            text="就绪",
            color=self.theme["text_secondary"],
            font_size=dp(13),
            halign="left",
            size_hint_x=None,
            width=dp(60),
        )
        st_box = BoxLayout(
            orientation="horizontal", size_hint_x=0.25, spacing=dp(6)
        )
        st_box.add_widget(self.status_dot)
        st_box.add_widget(self.st_label)

        timer_kwargs = dict(
            text="00:00",
            color=self.theme["text"],
            font_size=dp(22),
            bold=True,
            size_hint_x=0.35,
        )
        if self._has_monospace():
            timer_kwargs["font_name"] = "RobotoMono-Regular"
        self.timer_lbl = Label(**timer_kwargs)

        self.lbar = LevelBar(
            theme=self.theme, size_hint_x=0.4, size_hint_y=None, height=dp(40)
        )

        # 设置按钮
        self.btn_settings = Button(
            text="⚙",
            font_size=dp(18),
            size_hint=(None, None),
            size=(dp(40), dp(40)),
            background_normal="",
            background_color=self.theme["surface_variant"],
            color=self.theme["text"],
        )
        self.btn_settings.bind(on_press=self._open_settings)

        top.add_widget(st_box)
        top.add_widget(self.timer_lbl)
        top.add_widget(self.lbar)
        top.add_widget(self.btn_settings)

        # ─── 结果区 ─────────────────────────────
        self.res_grid = GridLayout(
            cols=1, spacing=dp(8), size_hint_y=None, padding=[dp(8), dp(8)]
        )
        self.res_grid.bind(
            minimum_height=self.res_grid.setter("height")
        )

        self.empty_lbl = Label(
            text="点击「开始」进行粤语实时翻译\n识别结果将显示在此处",
            color=self.theme["text_secondary"],
            font_size=dp(16),
            halign="center",
            valign="middle",
            size_hint_y=None,
            height=dp(120),
        )
        self.res_grid.add_widget(self.empty_lbl)

        self.scroll = ScrollView()
        self.scroll.add_widget(self.res_grid)

        # ─── 底栏 ───────────────────────────────
        bot = BoxLayout(
            size_hint_y=None, height=dp(80), spacing=dp(16), padding=[dp(16), dp(12)]
        )
        bot.bind(pos=self._draw_bot, size=self._draw_bot)

        self.btn_clr = Button(
            text="清空",
            font_size=dp(14),
            size_hint=(0.25, 1),
            background_normal="",
            background_color=self.theme["surface_variant"],
            color=self.theme["text"],
        )
        self.btn_clr.bind(on_press=self.on_clear)

        self.btn_rec = Button(
            text="开始",
            font_size=dp(18),
            bold=True,
            size_hint=(0.5, 1),
            background_normal="",
            background_color=self.theme["primary"],
            color=self.theme["text"],
        )
        self.btn_rec.bind(on_press=self.on_record_toggle)

        self.btn_export = Button(
            text="导出",
            font_size=dp(14),
            size_hint=(0.25, 1),
            background_normal="",
            background_color=self.theme["surface_variant"],
            color=self.theme["text"],
        )
        self.btn_export.bind(on_press=self._export_session)

        bot.add_widget(self.btn_clr)
        bot.add_widget(self.btn_rec)
        bot.add_widget(self.btn_export)

        self.add_widget(top)
        self.add_widget(self.scroll)
        self.add_widget(bot)

        # 模拟模式提示
        if not is_android():
            sim_lbl = Label(
                text="[模拟模式] 非 Android 环境 — 音频不可用，UI 正常演示",
                color=self.theme["accent"],
                font_size=dp(11),
                size_hint_y=None,
                height=dp(20),
            )
            self.add_widget(sim_lbl)

    def _has_monospace(self) -> bool:
        try:
            from kivy.core.text import LabelBase
            return "RobotoMono-Regular" in LabelBase._fonts
        except Exception:
            return False

    def _draw_top(self, w, *a):
        w.canvas.before.clear()
        with w.canvas.before:
            Color(*self.theme["surface"])
            Rectangle(pos=w.pos, size=w.size)

    def _draw_bot(self, w, *a):
        w.canvas.before.clear()
        with w.canvas.before:
            Color(*self.theme["surface"])
            Rectangle(pos=w.pos, size=w.size)

    def _on_resize(self, win, width, height):
        """屏幕旋转时重新布局。"""
        for child in self.res_grid.children:
            if isinstance(child, ResultCard):
                for lbl in child.children:
                    if isinstance(lbl, Label) and getattr(lbl, "_is_result_text", False):
                        lbl.text_size = (width - dp(56), None)

    def _open_settings(self, *args):
        popup = SettingsPopup(self.app)
        popup.open()

    def _show_toast(self, msg: str):
        toast = Label(
            text=msg,
            color=self.theme["accent"],
            font_size=dp(12),
            size_hint=(None, None),
            size=(dp(140), dp(28)),
            pos=(Window.width / 2 - dp(70), dp(80)),
        )
        self.add_widget(toast)
        anim = (
            Animation(opacity=1, duration=0.2)
            + Animation(opacity=1, duration=1.5)
            + Animation(opacity=0, duration=0.3)
        )
        anim.bind(
            on_complete=lambda *a: self.remove_widget(toast)
            if toast in self.children
            else None
        )
        anim.start(toast)

    # ─── 按钮事件 ────────────────────────────────
    def on_record_toggle(self, _):
        if self.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def on_clear(self, _):
        self.res_grid.clear_widgets()
        self.empty_lbl = Label(
            text="点击「开始」进行粤语实时翻译\n识别结果将显示在此处",
            color=self.theme["text_secondary"],
            font_size=dp(16),
            halign="center",
            valign="middle",
            size_hint_y=None,
            height=dp(120),
        )
        self.res_grid.add_widget(self.empty_lbl)
        self.last_text = ""
        self.results_count = 0

    def start_recording(self):
        if self.is_recording:
            return

        # 检查权限（Android 必须）
        if is_android() and not self.app._check_permissions():
            self.app._request_android_permissions()
            self._show_error("请先授予麦克风权限，再点击开始")
            return

        _lazy_load_modules()

        if not self._check_server():
            if not is_android():
                self._show_toast("模拟模式：服务器检查跳过")
            else:
                self._show_error("推理服务器未启动，请先在 Termux 运行 inference_server.py")
                return

        self.is_recording = True
        self.start_time = time.time()
        self._stop_event.clear()
        self._error_count = 0

        if _AudioProcessor:
            vad_mode = self.app.settings.get("vad_mode", 1)
            self.processor = _AudioProcessor(vad_mode=vad_mode)
            self.processor.reset()
        else:
            self.processor = None

        self.btn_rec.text = "停止"
        self.btn_rec.background_color = self.theme["error"]
        self.st_label.text = "录音中"
        self.st_label.color = self.theme["primary"]
        self.status_dot.is_active = True

        self.on_clear(None)

        # 创建新会话记录
        try:
            from history import HistoryManager
            self._session_id = HistoryManager().create_session()
        except Exception as e:
            logger.warning("创建历史会话失败: %s", e)
            self._session_id = None

        try:
            if is_android():
                if _AndroidAudioCapture:
                    sample_rate = self.app.settings.get("sample_rate", 16000)
                    self.capture = _AndroidAudioCapture(sample_rate=sample_rate)
                else:
                    self._show_error("Android 音频模块不可用")
                    self.stop_recording()
                    return
            else:
                self._show_toast("模拟模式：无真实录音")
                # PC 模拟：定时产生假数据用于 UI 测试
                self._timer_event = Clock.schedule_interval(self._simulate_chunk, 2.0)
                self._timer_event2 = Clock.schedule_interval(self._tick, 0.2)
                return
            self.capture.start()
        except Exception as e:
            logger.error("启动音频采集失败: %s", e)
            self._show_error(f"启动失败: {e}")
            self.stop_recording()
            return

        self._processing_thread = threading.Thread(
            target=self._loop, daemon=True, name="AudioLoop"
        )
        self._processing_thread.start()
        self._timer_event = Clock.schedule_interval(self._tick, 0.2)
        logger.info("录音已启动")

    def stop_recording(self):
        if not self.is_recording:
            return
        self.is_recording = False
        self._stop_event.set()

        with self._capture_lock:
            if self.capture:
                try:
                    self.capture.stop()
                except Exception:
                    pass
                self.capture = None

        if self._timer_event:
            self._timer_event.cancel()
            self._timer_event = None
        if self._timer_event2:
            self._timer_event2.cancel()
            self._timer_event2 = None

        self.btn_rec.text = "开始"
        self.btn_rec.background_color = self.theme["primary"]
        self.st_label.text = "已停止"
        self.st_label.color = self.theme["text_secondary"]
        self.status_dot.is_active = False
        logger.info("录音已停止")

    def _simulate_chunk(self, dt):
        """PC 模拟模式：产生随机电平变化。"""
        import random
        level = random.randint(10, 90)
        self.lbar.level = level
        # 偶尔产生一条假文本
        if random.random() < 0.3:
            fake_texts = [
                "呢度係一個測試",
                "你好，世界",
                "廣東話語音識別",
                "模擬模式下顯示",
            ]
            self._add(random.choice(fake_texts), 0)

    # ─── 处理循环 ────────────────────────────────
    def _loop(self):
        while not self._stop_event.is_set():
            try:
                with self._capture_lock:
                    cap = self.capture
                    running = cap is not None and cap.is_running
                if not running:
                    time.sleep(0.05)
                    continue

                chunk = cap.get_chunk(timeout=0.5) if cap else None
                if chunk is None:
                    time.sleep(0.05)
                    continue

                if self.processor:
                    p = self.processor.process(chunk)
                    Clock.schedule_once(
                        lambda dt, lvl=p.level_db: setattr(
                            self.lbar,
                            "level",
                            min(100, max(0, (lvl + 60) / 60 * 100)),
                        ),
                        0,
                    )
                    if p.has_speech:
                        text = self._transcribe(p.audio)
                        if text:
                            Clock.schedule_once(
                                lambda dt, t=text, sid=p.segment_id: self._add(t, sid),
                                0,
                            )
                else:
                    text = self._transcribe(chunk)
                    if text:
                        Clock.schedule_once(lambda dt, t=text: self._add(t, 0), 0)
            except Exception as e:
                logger.error("处理异常: %s", e)
                time.sleep(0.1)
        logger.info("处理线程退出")

    def _transcribe(self, wav: bytes) -> Optional[str]:
        self._error_count = 0
        url = f"{self.app.inference_url}/transcribe"
        try:
            d = json.dumps({"wav": base64.b64encode(wav).decode()}).encode()
            req = urllib.request.Request(
                url, data=d, headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                t = json.loads(r.read()).get("text", "").strip()
            self._error_count = 0
            return t or None
        except Exception as e:
            logger.warning("推理请求失败: %s", e)
            self._error_count += 1
            if self._error_count <= self._max_retries:
                time.sleep(0.5 * self._error_count)
                return self._transcribe(wav)
            else:
                Clock.schedule_once(
                    lambda dt: self._show_error("推理服务器连接失败，已重试 3 次"), 0
                )
        return None

    def _check_server(self) -> bool:
        url = f"{self.app.inference_url}/health"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=3) as r:
                return json.loads(r.read()).get("status") == "ok"
        except Exception:
            return False

    # ─── UI 更新 ────────────────────────────────
    def _tick(self, _):
        t = int(time.time() - self.start_time)
        self.timer_lbl.text = f"{t // 60:02d}:{t % 60:02d}"

    def _add(self, text: str, seg_id: int):
        if self.empty_lbl and self.empty_lbl in self.res_grid.children:
            self.res_grid.remove_widget(self.empty_lbl)
            self.empty_lbl = None

        # 去重：如果新文本是旧文本的开头或旧文本是新文本的开头，更新最后一个
        if self.last_text and (
            text.startswith(self.last_text) or self.last_text.startswith(text)
        ):
            children = list(self.res_grid.children)
            if children and hasattr(children[-1], "_text_to_copy"):
                children[-1]._text_to_copy = text
                for child in children[-1].children:
                    if isinstance(child, Label) and child.font_size == dp(16):
                        child.text = text
                self.last_text = text
                # 更新历史
                self._save_to_history(text)
                return

        ts = time.strftime("%H:%M:%S")
        card = ResultCard(
            text=text,
            timestamp=ts,
            theme=self.theme,
            size_hint_x=1,
        )
        self.res_grid.add_widget(card, index=len(self.res_grid.children))
        self.results_count += 1
        self.last_text = text

        # 自动滚动到底部
        Clock.schedule_once(lambda dt: setattr(self.scroll, "scroll_y", 0), 0.05)

        # 保存到历史
        self._save_to_history(text)

    def _save_to_history(self, text: str):
        if self._session_id is not None:
            try:
                from history import HistoryManager
                HistoryManager().add_result(self._session_id, text)
            except Exception as e:
                logger.debug("保存历史失败: %s", e)

    def _show_error(self, msg: str):
        logger.error(msg)
        if self.empty_lbl and self.empty_lbl in self.res_grid.children:
            self.res_grid.remove_widget(self.empty_lbl)
            self.empty_lbl = None
        lbl = Label(
            text=msg,
            color=self.theme["error"],
            font_size=dp(13),
            size_hint_y=None,
            height=dp(36),
            halign="center",
        )
        self.res_grid.add_widget(lbl, index=0)
        Clock.schedule_once(
            lambda dt: self.res_grid.remove_widget(lbl)
            if lbl in self.res_grid.children
            else None,
            4,
        )

    def _export_session(self, *args):
        texts: List[str] = []
        for child in reversed(list(self.res_grid.children)):
            if hasattr(child, "_text_to_copy"):
                texts.append(child._text_to_copy)
        if not texts:
            self._show_toast("没有可导出的内容")
            return
        content = f"粤语实时翻译导出\n时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        content += "\n".join(texts)
        try:
            from utils import FileExporter
            path = FileExporter.export_text(content)
            self._show_toast(f"已导出: {path}")
        except Exception as e:
            logger.warning("导出失败: %s", e)
            self._show_toast("导出失败")

    def refresh_theme(self, theme: Dict[str, Any]):
        """动态刷新主题。"""
        self.theme = theme
        self._build_ui()


# ── App ───────────────────────────────────────────────────
class TranslatorApp(App):
    theme_name = StringProperty("dark")
    inference_url = StringProperty(INFERENCE_URL)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.ui: Optional[TranslatorUI] = None
        self._settings = None
        self._theme = THEMES["dark"]
        self._permissions_granted = False

    def _request_android_permissions(self):
        """运行时申请 Android 权限（RECORD_AUDIO + WRITE_EXTERNAL_STORAGE）。"""
        if not is_android():
            self._permissions_granted = True
            return True
        try:
            from jnius import autoclass
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            activity = PythonActivity.mActivity
            PackageManager = autoclass('android.content.pm.PackageManager')

            # 检查哪些权限还没授予
            needed = []
            for perm in ["android.permission.RECORD_AUDIO",
                         "android.permission.WRITE_EXTERNAL_STORAGE"]:
                try:
                    if activity.checkSelfPermission(perm) != PackageManager.PERMISSION_GRANTED:
                        needed.append(perm)
                except Exception:
                    needed.append(perm)

            if needed:
                activity.requestPermissions(needed, 0)
                logger.info("已申请权限: %s", needed)
                return False
            else:
                self._permissions_granted = True
                return True
        except Exception as e:
            logger.error("权限申请异常: %s", e)
            return False

    def _check_permissions(self):
        """检查当前是否已授予必要权限。"""
        if not is_android():
            return True
        try:
            from jnius import autoclass
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            activity = PythonActivity.mActivity
            PackageManager = autoclass('android.content.pm.PackageManager')
            for perm in ["android.permission.RECORD_AUDIO",
                         "android.permission.WRITE_EXTERNAL_STORAGE"]:
                if activity.checkSelfPermission(perm) != PackageManager.PERMISSION_GRANTED:
                    return False
            self._permissions_granted = True
            return True
        except Exception:
            return False

    @property
    def theme(self) -> Dict[str, Any]:
        return self._theme

    @property
    def settings(self):
        if self._settings is None:
            try:
                from settings import SettingsManager
                self._settings = SettingsManager()
                self.theme_name = self._settings.get("theme", "dark")
                self._theme = THEMES.get(self.theme_name, THEMES["dark"])
            except Exception as e:
                logger.warning("SettingsManager 加载失败: %s", e)
                self._settings = _DummySettings()
        return self._settings

    def build(self):
        self.title = "粤语实时翻译"
        self._apply_theme()
        self._update_inference_url()
        self.ui = TranslatorUI(app=self)
        return self.ui

    def _apply_theme(self):
        self._theme = THEMES.get(self.theme_name, THEMES["dark"])
        Window.clearcolor = self._theme["bg"]
        if self.ui:
            self.ui.refresh_theme(self._theme)

    def _update_inference_url(self):
        host = self.settings.get("inference_host", INFERENCE_HOST)
        port = self.settings.get("inference_port", INFERENCE_PORT)
        self.inference_url = f"http://{host}:{port}"

    def on_start(self):
        logger.info("应用已启动 | 启动标记写入: %d 个路径", _MARKERS_WRITTEN)
        # 启动时自动申请权限
        if is_android() and not self._permissions_granted:
            self._request_android_permissions()
            # 延迟再次检查权限状态（给用户点击"允许"的时间）
            Clock.schedule_once(lambda dt: self._recheck_permissions(), 2.0)

    def _recheck_permissions(self):
        """延迟检查权限，如果仍未授予则提示用户。"""
        if is_android() and not self._check_permissions():
            if self.ui:
                self.ui._show_error("需要麦克风权限才能录音，请在系统设置中开启")

    def on_stop(self):
        if self.ui and self.ui.is_recording:
            self.ui.stop_recording()


# ── 虚拟设置（fallback）───────────────────────────────────
class _DummySettings:
    """当 settings.py 加载失败时的降级实现。"""
    _data: Dict[str, Any] = {}

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value):
        self._data[key] = value

    def save(self):
        pass


# ── 入口 ──────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        logger.info("=" * 40)
        logger.info("粤语实时翻译 - Kivy Android")
        logger.info("推理: %s", INFERENCE_URL)
        logger.info("启动标记写入路径: %d", _MARKERS_WRITTEN)
        logger.info("=" * 40)
        TranslatorApp().run()
    except Exception as e:
        _write_crash_log(type(e), e, e.__traceback__)
        time.sleep(5)
