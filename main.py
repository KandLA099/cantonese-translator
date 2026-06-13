#!/usr/bin/env python3
"""
main.py — 粤语实时翻译 Android 主入口 (webview bootstrap)
============================================================

架构:
  1. 启动多线程 HTTP 服务器 (localhost:8080)
  2. WebView 加载 http://localhost:8080
  3. 前端通过 fetch() 轮询 API
  4. Python 端通过 pyjnius 调用 Android AudioRecord
  5. 音频通过 HTTP POST 发送到 Termux 推理服务器 (端口 5001)

API:
  GET  /              → index.html
  GET  /app.js        → app.js
  GET  /style.css     → style.css
  POST /api/start     → 开始录音
  POST /api/stop      → 停止录音
  GET  /api/status    → {is_recording, elapsed, level, server_connected}
  GET  /api/results   → {results: [{id, text, timestamp}]}
  POST /api/clear     → 清空结果
"""

import base64
import json
import logging
import os
import sys
import threading
import time
import traceback
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Optional, List, Dict, Any

# ── 崩溃日志捕获（最早执行）─────────────────────────────
_APP_PKG = "com.cantonesetranslator"

def _get_log_paths():
    candidates = [
        f"/storage/emulated/0/Download/{_APP_PKG}",
        "/sdcard/Download",
        os.environ.get("EXTERNAL_STORAGE"),
        os.environ.get("ANDROID_PRIVATE"),
        os.environ.get("HOME"),
        os.path.expanduser("~"),
        "/data/local/tmp",
        "/sdcard",
        ".",
    ]
    return [p for p in candidates if p]

def _try_write(path: str, text: str) -> bool:
    try:
        dir_path = os.path.dirname(path) if os.path.dirname(path) else "."
        os.makedirs(dir_path, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)
        return True
    except Exception:
        return False

def _write_crash(exc_type, exc_value, exc_tb):
    tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    print(f"\n[CRASH] {exc_type.__name__}: {exc_value}", flush=True)
    text = f"\n{'='*60}\nCRASH {time.strftime('%Y-%m-%d %H:%M:%S')}\n{'='*60}\n{tb}\n"
    for p in _get_log_paths():
        if p:
            _try_write(os.path.join(p, "crash.log"), text)

sys.excepthook = _write_crash

# ── 日志配置 ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

for p in _get_log_paths():
    try:
        fh = logging.FileHandler(os.path.join(p, "app.log"), encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logging.getLogger().addHandler(fh)
        break
    except Exception:
        continue

# ── 配置 ─────────────────────────────────────────────────
INFERENCE_HOST = os.environ.get("INFERENCE_HOST", "127.0.0.1")
INFERENCE_PORT = os.environ.get("INFERENCE_PORT", "5001")
INFERENCE_URL = f"http://{INFERENCE_HOST}:{INFERENCE_PORT}"
HTTP_PORT = 8080

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

# ── 全局状态 ─────────────────────────────────────────────
_state = {
    "is_recording": False,
    "start_time": 0.0,
    "audio_level": 0,
    "server_connected": False,
    "results": [],
    "result_counter": 0,
    "lock": threading.Lock(),
}

# ── 推理服务器检测 ───────────────────────────────────────
def _check_server() -> bool:
    try:
        req = urllib.request.Request(f"{INFERENCE_URL}/health", method="GET")
        with urllib.request.urlopen(req, timeout=2) as r:
            data = json.loads(r.read())
            return data.get("status") == "ok"
    except Exception:
        return False

# ── 音频转发到推理服务器 ─────────────────────────────────
def _transcribe(wav_bytes: bytes) -> Optional[str]:
    try:
        data = json.dumps({"wav": base64.b64encode(wav_bytes).decode()}).encode()
        req = urllib.request.Request(
            f"{INFERENCE_URL}/transcribe",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.loads(r.read())
            text = result.get("text", "").strip()
            return text if text else None
    except Exception as e:
        logger.warning("推理请求失败: %s", e)
        return None

# ── 添加识别结果 ─────────────────────────────────────────
def _add_result(text: str):
    with _state["lock"]:
        _state["result_counter"] += 1
        _state["results"].append({
            "id": _state["result_counter"],
            "text": text,
            "timestamp": time.strftime("%H:%M:%S"),
        })

# ── Android 录音管理 ─────────────────────────────────────
class AudioRecorder:
    def __init__(self):
        self.capture = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._is_android = self._detect_android()

    def _detect_android(self) -> bool:
        if os.environ.get("ANDROID_ARGUMENT"):
            return True
        try:
            import platform
            return platform.system() == "Linux" and os.path.exists("/system/build.prop")
        except Exception:
            return False

    def start(self):
        if not self._is_android:
            logger.info("非 Android 环境，启动模拟录音")
            self._start_simulation()
            return

        try:
            from android_audio import AndroidAudioCapture
            self.capture = AndroidAudioCapture(sample_rate=16000)
            self.capture.start()
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._record_loop, daemon=True)
            self._thread.start()
            logger.info("录音已启动")
        except Exception as e:
            logger.error("启动录音失败: %s", e)
            raise

    def stop(self):
        self._stop_event.set()
        if self.capture:
            try:
                self.capture.stop()
            except Exception:
                pass
            self.capture = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        logger.info("录音已停止")

    def get_level(self) -> int:
        import random
        if _state["is_recording"]:
            return random.randint(10, 80)
        return 0

    def _record_loop(self):
        while not self._stop_event.is_set():
            try:
                if not self.capture or not self.capture.is_running:
                    time.sleep(0.05)
                    continue

                chunk = self.capture.get_chunk(timeout=0.5)
                if not chunk:
                    continue

                text = _transcribe(chunk)
                if text:
                    _add_result(text)
                    logger.info("识别结果: %s", text)

                with _state["lock"]:
                    _state["audio_level"] = self.get_level()

            except Exception as e:
                logger.error("录音循环异常: %s", e)
                time.sleep(0.1)

    def _start_simulation(self):
        self._stop_event.clear()
        import random

        def simulate():
            texts = ["你好", "今天天气很好", "粤语测试", "识别成功"]
            while not self._stop_event.is_set():
                time.sleep(2)
                with _state["lock"]:
                    _state["audio_level"] = random.randint(20, 70)
                if random.random() < 0.5:
                    _add_result(random.choice(texts))

        self._thread = threading.Thread(target=simulate, daemon=True)
        self._thread.start()
        logger.info("模拟录音已启动")


_recorder = AudioRecorder()

# ── HTTP 请求处理 ────────────────────────────────────────
class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logger.debug(fmt % args)

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: str, content_type: str):
        try:
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = self.path

        if path == "/" or path == "/index.html":
            self._send_file(os.path.join(ASSETS_DIR, "index.html"), "text/html; charset=utf-8")
        elif path == "/app.js":
            self._send_file(os.path.join(ASSETS_DIR, "app.js"), "application/javascript; charset=utf-8")
        elif path == "/style.css":
            self._send_file(os.path.join(ASSETS_DIR, "style.css"), "text/css; charset=utf-8")
        elif path == "/api/status":
            with _state["lock"]:
                elapsed = int(time.time() - _state["start_time"]) if _state["is_recording"] else 0
                self._send_json({
                    "is_recording": _state["is_recording"],
                    "elapsed": elapsed,
                    "level": _state["audio_level"],
                    "server_connected": _check_server(),
                })
        elif path == "/api/results":
            with _state["lock"]:
                self._send_json({"results": list(_state["results"])})
        else:
            self.send_error(404)

    def do_POST(self):
        path = self.path

        if path == "/api/start":
            if _state["is_recording"]:
                self._send_json({"success": False, "message": "已在录音中"})
                return

            _state["is_recording"] = True
            _state["start_time"] = time.time()
            _state["audio_level"] = 0

            try:
                _recorder.start()
                self._send_json({"success": True, "message": "录音已开始"})
            except Exception as e:
                _state["is_recording"] = False
                logger.error("开始录音失败: %s", e)
                self._send_json({"success": False, "message": str(e)})

        elif path == "/api/stop":
            if not _state["is_recording"]:
                self._send_json({"success": False, "message": "未在录音"})
                return

            _state["is_recording"] = False
            _state["audio_level"] = 0
            _recorder.stop()
            self._send_json({"success": True, "message": "录音已停止"})

        elif path == "/api/clear":
            with _state["lock"]:
                _state["results"].clear()
                _state["result_counter"] = 0
            self._send_json({"success": True, "message": "已清空"})

        else:
            self.send_error(404)


# ── 入口（模块级别直接启动，p4a 不以 __main__ 运行）────
def _run_server():
    """启动 HTTP 服务器，保持运行。"""
    logger.info("=" * 50)
    logger.info("粤语实时翻译 — webview 版")
    logger.info(f"推理服务器: {INFERENCE_URL}")
    logger.info(f"HTTP 服务器: 0.0.0.0:{HTTP_PORT}")
    logger.info("=" * 50)

    try:
        # 绑定到 0.0.0.0 确保 WebView 能访问
        server = ThreadedHTTPServer(("0.0.0.0", HTTP_PORT), Handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        logger.info("HTTP 服务器已启动在 0.0.0.0:%d", HTTP_PORT)
    except Exception as e:
        logger.error("启动 HTTP 服务器失败: %s", e)
        _write_crash(type(e), e, e.__traceback__)
        return

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在停止...")
    finally:
        server.shutdown()
        _recorder.stop()


# p4a webview bootstrap 导入时直接启动（不是 __main__）
try:
    _run_server()
except Exception as e:
    _write_crash(type(e), e, e.__traceback__)
    time.sleep(5)
