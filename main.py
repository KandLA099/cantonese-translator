#!/usr/bin/env python3
"""
main.py — 粤语实时翻译 Android 主程序 (Kivy SDL2)
=================================================

架构 vs 旧版（Flask+WebView）:
  - 删掉 Flask/SocketIO/WebView，换 Kivy 原生 UI
  - 用 SDL2 bootstrap，兼容性远好于 webview
  - 音频采集走 pyjnius AudioRecord
  - 推理走 HTTP 请求 Termux 上的推理服务器
"""

import base64
import json
import logging
import os
import sys
import threading
import time
import traceback
import queue
import urllib.request
from typing import Optional

# Kivy 环境变量（必须在 kivy import 前）
os.environ.setdefault("KIVY_NO_ARGS", "1")

import kivy
kivy.require("2.3.0")

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.gridlayout import GridLayout
from kivy.uix.widget import Widget
from kivy.utils import get_color_from_hex

# ── 崩溃日志捕获 ──────────────────────────────────────
def _get_log_paths():
    """返回所有可能的日志路径（全部尝试写入）。"""
    pkg = "com.cantonesetranslator"
    return [
        os.environ.get("ANDROID_PRIVATE"),
        f"/storage/emulated/0/Android/data/{pkg}/files",
        os.environ.get("EXTERNAL_STORAGE"),
        "/data/local/tmp",
        "/sdcard",
    ]

def _try_write_log(path, text):
    if not path:
        return False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)
        return True
    except Exception:
        return False

def _write_crash_log(exc_type, exc_value, exc_tb):
    tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    # stdout 输出（adb logcat 可见）
    print(f"\n!!!!! CRASH {exc_type.__name__}: {exc_value}", flush=True)
    for line in tb_text.splitlines():
        print(f"[CRASH] {line}", flush=True)
    # 写入所有可能的路径
    text = f"\n{'='*60}\nCRASH at {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
    text += f"[PID {os.getpid()}] {exc_type.__name__}: {exc_value}\n{'='*60}\n"
    text += tb_text + "\n"
    for p in _get_log_paths():
        if p:
            _try_write_log(os.path.join(p, "cantonese_crash.log"), text)

# 立即写一个启动标记（证明 Python 启动到了这里）
_MARKERS_WRITTEN = 0
for _p in _get_log_paths():
    if _p and _try_write_log(os.path.join(_p, "cantonese_started.txt"),
                              f"STARTED at {time.strftime('%Y-%m-%d %H:%M:%S')}\n"):
        _MARKERS_WRITTEN += 1

sys.excepthook = _write_crash_log

# ── 日志 ──────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("main")

# 日志也写入文件
for _p in _get_log_paths():
    if _p:
        try:
            _fh = logging.FileHandler(os.path.join(_p, "cantonese_crash.log"), encoding="utf-8")
            _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
            logging.getLogger().addHandler(_fh)
            break
        except Exception:
            continue

# ── 配置 ──────────────────────────────────────────────
INFERENCE_URL = f"http://{os.environ.get('INFERENCE_HOST', '127.0.0.1')}:{os.environ.get('INFERENCE_PORT', '5001')}"

# ── Android 检测 ─────────────────────────────────────
_HAS_ANDROID = False
try:
    from android_audio import AndroidAudioCapture
    _HAS_ANDROID = True
    logger.info("Android 音频模块已加载")
except ImportError as e:
    logger.warning("Android 音频模块不可用: %s", e)

from audio_processor import AudioProcessor

# ============================================================
# 颜色
# ============================================================
BG = get_color_from_hex("#1A1D23")
SURFACE = get_color_from_hex("#262A34")
GREEN = get_color_from_hex("#4CAF50")
GREEN_D = get_color_from_hex("#388E3C")
TEXT = get_color_from_hex("#E8EAED")
DIM = get_color_from_hex("#9AA0A6")
RED = get_color_from_hex("#FF4D4F")
YELLOW = get_color_from_hex("#FAAD14")


# ============================================================
# 小部件
# ============================================================

class LevelBar(Widget):
    """音频电平指示条（竖条）。"""
    level = 0.0  # 0-100

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(pos=self._redraw, size=self._redraw, level=self._redraw)

    def _redraw(self, *args):
        self.canvas.clear()
        with self.canvas:
            Color(0.2, 0.2, 0.2, 1)
            Rectangle(pos=self.pos, size=self.size)
            h = self.height * min(self.level / 100.0, 1.0)
            if self.level > 80:
                Color(*RED)
            elif self.level > 50:
                Color(*YELLOW)
            else:
                Color(*GREEN)
            Rectangle(pos=(self.x, self.y), size=(self.width, h))


# ============================================================
# 主 UI
# ============================================================

class TranslatorUI(BoxLayout):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = 0
        self.spacing = 0

        # 状态
        self.is_recording = False
        self.start_time = 0
        self.last_text = ""
        self.results_count = 0

        # 音频
        self.capture = None
        self.processor = AudioProcessor()
        self._stop_event = threading.Event()
        self._processing_thread = None
        self._timer_event = None

        self._build_ui()

    def _build_ui(self):
        # ─── 顶栏 ───────────────────────────────
        top = BoxLayout(size_hint_y=None, height=dp(56), spacing=dp(8), padding=[dp(12), dp(8)])
        top.bind(pos=self._draw_top, size=self._draw_top)

        self.dot = Label(text="●", color=DIM, font_size=dp(14), size_hint_x=None, width=dp(20))
        self.st_label = Label(text="就绪", color=DIM, font_size=dp(13), halign="left")
        st_box = BoxLayout(orientation="horizontal", size_hint_x=0.3, spacing=dp(4))
        st_box.add_widget(self.dot)
        st_box.add_widget(self.st_label)

        self.timer = Label(text="00:00", color=TEXT, font_size=dp(22), bold=True, size_hint_x=0.4)

        self.lbar = LevelBar(size_hint_x=0.3, size_hint_y=None, height=dp(40))

        top.add_widget(st_box)
        top.add_widget(self.timer)
        top.add_widget(self.lbar)

        # ─── 结果区 ─────────────────────────────
        self.res_grid = GridLayout(cols=1, spacing=dp(2), size_hint_y=None, padding=[0, dp(8)])
        self.res_grid.bind(minimum_height=self.res_grid.setter("height"))

        self.empty_lbl = Label(
            text="点击「开始」进行粤语实时翻译\n识别结果将显示在此处",
            color=DIM, font_size=dp(16), halign="center", valign="middle",
        )
        self.res_grid.add_widget(self.empty_lbl)

        sv = ScrollView()
        sv.add_widget(self.res_grid)

        # ─── 底栏 ───────────────────────────────
        bot = BoxLayout(size_hint_y=None, height=dp(80), spacing=dp(20), padding=[dp(20), dp(10)])
        bot.bind(pos=self._draw_bot, size=self._draw_bot)

        self.btn_clr = Button(text="清空", font_size=dp(16), size_hint=(0.3, 1),
                              background_normal="", background_color=get_color_from_hex("#444"), color=TEXT)
        self.btn_clr.bind(on_press=self.on_clear)

        self.btn_rec = Button(text="开始", font_size=dp(20), size_hint=(0.5, 1),
                              background_normal="", background_color=GREEN, color=TEXT)
        self.btn_rec.bind(on_press=self.on_record_toggle)

        bot.add_widget(self.btn_clr)
        bot.add_widget(self.btn_rec)
        bot.add_widget(Widget(size_hint_x=0.2))

        self.add_widget(top)
        self.add_widget(sv)
        self.add_widget(bot)

    # ─── 顶栏/底栏背景 ───────────────────────────
    def _draw_top(self, w, *a):
        w.canvas.before.clear()
        with w.canvas.before:
            Color(*SURFACE)
            Rectangle(pos=w.pos, size=w.size)

    def _draw_bot(self, w, *a):
        w.canvas.before.clear()
        with w.canvas.before:
            Color(*SURFACE)
            Rectangle(pos=w.pos, size=w.size)

    # ─── 按钮事件 ────────────────────────────────
    def on_record_toggle(self, _):
        if self.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def on_clear(self, _):
        self.res_grid.clear_widgets()
        self.empty_lbl = Label(text="点击「开始」进行粤语实时翻译\n识别结果将显示在此处",
                               color=DIM, font_size=dp(16), halign="center", valign="middle")
        self.res_grid.add_widget(self.empty_lbl)
        self.last_text = ""
        self.results_count = 0

    def start_recording(self):
        if self.is_recording:
            return
        if not self._check_server():
            self._show_error("推理服务器未启动，请先在 Termux 运行 inference_server.py")
            return

        self.is_recording = True
        self.start_time = time.time()
        self._stop_event.clear()
        self.processor.reset()

        self.btn_rec.text = "停止"
        self.btn_rec.background_color = RED
        self.st_label.text = "录音中"
        self.st_label.color = GREEN
        self.dot.color = GREEN

        self.on_clear(None)

        try:
            if _HAS_ANDROID:
                self.capture = AndroidAudioCapture()
            else:
                self._show_error("非 Android 环境")
                self.stop_recording()
                return
            self.capture.start()
        except Exception as e:
            logger.error("启动音频采集失败: %s", e)
            self._show_error(f"启动失败: {e}")
            self.stop_recording()
            return

        self._processing_thread = threading.Thread(target=self._loop, daemon=True)
        self._processing_thread.start()
        self._timer_event = Clock.schedule_interval(self._tick, 0.2)
        logger.info("录音已启动")

    def stop_recording(self):
        if not self.is_recording:
            return
        self.is_recording = False
        self._stop_event.set()

        if self.capture:
            try:
                self.capture.stop()
            except Exception:
                pass
            self.capture = None

        if self._timer_event:
            self._timer_event.cancel()
            self._timer_event = None

        self.btn_rec.text = "开始"
        self.btn_rec.background_color = GREEN
        self.st_label.text = "已停止"
        self.st_label.color = DIM
        self.dot.color = DIM
        logger.info("录音已停止")

    # ─── 处理循环 ────────────────────────────────
    def _loop(self):
        while self.is_recording and not self._stop_event.is_set():
            try:
                chunk = self.capture.get_chunk(timeout=0.5) if self.capture and self.capture.is_running else None
                if chunk is None:
                    time.sleep(0.05)
                    continue
                p = self.processor.process(chunk)
                Clock.schedule_once(lambda dt: setattr(self.lbar, "level", min(100, max(0, (p.level_db + 60) / 60 * 100))))
                if p.has_speech:
                    text = self._transcribe(p.audio)
                    if text:
                        Clock.schedule_once(lambda dt, t=text, sid=p.segment_id: self._add(t, sid))
            except Exception as e:
                logger.error("处理异常: %s", e)
                time.sleep(0.1)
        logger.info("处理线程退出")

    def _transcribe(self, wav):
        try:
            d = json.dumps({"wav": base64.b64encode(wav).decode()}).encode()
            req = urllib.request.Request(f"{INFERENCE_URL}/transcribe", data=d,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                t = json.loads(r.read()).get("text", "").strip()
            return t or None
        except Exception as e:
            logger.warning("推理请求失败: %s", e)
        return None

    def _check_server(self):
        try:
            req = urllib.request.Request(f"{INFERENCE_URL}/health")
            with urllib.request.urlopen(req, timeout=3) as r:
                return json.loads(r.read()).get("status") == "ok"
        except Exception:
            return False

    # ─── UI 更新 ────────────────────────────────
    def _tick(self, _):
        t = int(time.time() - self.start_time)
        self.timer.text = f"{t // 60:02d}:{t % 60:02d}"

    def _add(self, text, seg_id):
        if self.empty_lbl in self.res_grid.children:
            self.res_grid.remove_widget(self.empty_lbl)
        # 去重
        if self.last_text and (text.startswith(self.last_text) or self.last_text.startswith(text)):
            last = list(self.res_grid.children)[-1]
            if isinstance(last, Label):
                last.text = text
                last.color = TEXT
                self.last_text = text
            return
        lbl = Label(text=text, color=TEXT, font_size=dp(18), halign="left", valign="middle",
                    size_hint_y=None, text_size=(Window.width - dp(24), None))
        lbl.bind(texture_size=lambda *x: setattr(lbl, "height", max(dp(40), lbl.texture_size[1] + dp(8))))
        self.res_grid.add_widget(lbl)
        self.results_count += 1
        self.last_text = text
        Clock.schedule_once(lambda dt: setattr(self.parent if hasattr(self, "parent") else None,
                                                "scroll_y", 0) if False else None, 0.05)
        # scroll to bottom
        sv = self.parent if isinstance(self.parent, ScrollView) else None
        if sv:
            Clock.schedule_once(lambda dt: setattr(sv, "scroll_y", 0), 0.05)

    def _show_error(self, msg):
        logger.error(msg)
        lbl = Label(text=msg, color=RED, font_size=dp(14), size_hint_y=None, height=dp(30))
        self.res_grid.add_widget(lbl)
        Clock.schedule_once(lambda dt: self.res_grid.remove_widget(lbl) if lbl in self.res_grid.children else None, 4)


# ============================================================
# App
# ============================================================

class TranslatorApp(App):
    def build(self):
        self.title = "粤语实时翻译"
        Window.clearcolor = BG
        return TranslatorUI()

    def on_start(self):
        logger.info("应用已启动 | 启动标记写入: %d 个路径", _MARKERS_WRITTEN)


# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    try:
        logger.info("=" * 40)
        logger.info("粤语实时翻译 - Kivy Android")
        logger.info("推理: %s", INFERENCE_URL)
        logger.info("启动标记写入路径: %d/5", _MARKERS_WRITTEN)
        logger.info("=" * 40)
        TranslatorApp().run()
    except Exception as e:
        _write_crash_log(type(e), e, e.__traceback__)
        time.sleep(5)
