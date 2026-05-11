#!/usr/bin/env python3
"""
main_android.py — Android 应用主入口
=====================================

架构说明:
  本 APK 只做两件事:
    1. 音频采集 (通过 android_audio.AndroidAudioCapture)
    2. 显示 Web UI (通过 Flask-SocketIO)

  ASR 推理由独立进程处理 — inference_server.py 在 Termux 中运行。
  APK 采集音频后，通过 HTTP 发送到推理服务器，返回结果后显示在 UI 上。

工作原理:
  APK (端口 5000)         推理服务器 (端口 5001)
  ┌─────────────────┐     ┌──────────────────────┐
  │ Web UI          │     │ onnxruntime          │
  │ (SocketIO 前端) │     │ SenseVoice 模型       │
  │                 │     │                      │
  │ AudioRecord     │────→│ POST /transcribe     │
  │ 采集音频         │     │ 返回识别文字          │
  └─────────────────┘     └──────────────────────┘

使用:
  1. 在 Termux 中启动推理服务器:
     python inference_server.py

  2. 启动 APK（自动连接推理服务器）
"""

import base64
import logging
import os
import sys
import threading
import time
import traceback
from typing import Optional

# ── 崩溃日志捕获（最早执行，确保闪退也能记录） ──────────
_CRASH_LOG_PATH = "/sdcard/cantonese_crash.log"

def _write_crash_log(exc_type, exc_value, exc_tb):
    """未捕获异常时写入崩溃日志到 /sdcard/。"""
    try:
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        with open(_CRASH_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"CRASH at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"{'='*60}\n")
            f.write(tb_text)
            f.write("\n")
        # 也打印到 stdout（让 adb logcat 也能看到）
        print(f"[CRASH] {exc_type.__name__}: {exc_value}")
        print(tb_text)
    except Exception:
        pass  # 日志写入失败就算了，别再抛异常

# 设置全局未捕获异常处理
sys.excepthook = _write_crash_log

# ── 日志 ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main_android")

# ── 同时将日志写入文件（/sdcard/ 下可查看） ──────────
try:
    _file_handler = logging.FileHandler(_CRASH_LOG_PATH, encoding="utf-8")
    _file_handler.setLevel(logging.INFO)
    _file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    logging.getLogger().addHandler(_file_handler)
    logger.info("崩溃日志已启用，写入: %s", _CRASH_LOG_PATH)
except Exception as e:
    logger.warning("无法创建文件日志: %s（非 Android 环境？）", e)

# 降低第三方库日志噪音
logging.getLogger("socketio").setLevel(logging.WARNING)
logging.getLogger("engineio").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# ── 配置 ──────────────────────────────────────────────
INFERENCE_HOST = os.environ.get("INFERENCE_HOST", "127.0.0.1")
INFERENCE_PORT = int(os.environ.get("INFERENCE_PORT", "5001"))
INFERENCE_URL = f"http://{INFERENCE_HOST}:{INFERENCE_PORT}"

# ── Android 检测 ─────────────────────────────────────
try:
    from android_audio import AndroidAudioCapture, get_audio_capture
    _HAS_ANDROID = True
except ImportError:
    _HAS_ANDROID = False
    AndroidAudioCapture = None

from audio_capture import AudioCapture
from audio_processor import AudioProcessor
from web_server import TranslationServer


class AndroidTranscriber:
    """
    Android 端主控制器。

    采集音频 → 发送到推理服务器 → 收到文字 → 显示在 UI 上。

    与桌面版 Transcriber 的区别:
      - 不加载 ASR 模型（推理在 Termux 上）
      - 通过 HTTP 发送音频到推理服务器
      - 音频采集使用 Android AudioRecord
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5000,
        inference_url: str = INFERENCE_URL,
        debug: bool = False,
    ):
        self.inference_url = inference_url

        # 音频采集
        self.capture: Optional[AudioCapture] = None
        self.processor = AudioProcessor()

        # Web 服务器
        self.server = TranslationServer(host=host, port=port, debug=debug)

        # 状态
        self._recording = False
        self._processing_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # 注册 WebSocket 回调
        self.server.on_start_recording = self._on_start_recording
        self.server.on_stop_recording = self._on_stop_recording
        self.server.on_clear_results = self._on_clear_results

    def run(self):
        """启动 Web 服务器（阻塞）。"""
        print()
        print("=" * 50)
        print("  粤语实时翻译 - Android 版")
        print(f"  Web UI:      http://{self.server.host}:{self.server.port}")
        print(f"  推理服务器:  {self.inference_url}")
        print("=" * 50)
        print()
        self.server.run()

    # ── WebSocket 回调 ────────────────────────────────

    def _on_start_recording(self):
        if self._recording:
            logger.warning("已在录音中")
            return

        # 检查推理服务器是否可用
        if not self._check_inference_server():
            self.server.publish_status(error="推理服务器未启动，请先在 Termux 中运行 inference_server.py")
            return

        logger.info(">>> 开始录音")
        self._recording = True
        self._stop_event.clear()
        self.processor.reset()

        # 启动 Android 音频采集
        if _HAS_ANDROID:
            logger.info("使用 Android AudioRecord 采集")
            self.capture = AndroidAudioCapture()
        else:
            logger.info("使用桌面版音频采集")
            self.capture = AudioCapture()

        self.capture.start()

        # 启动处理线程
        self._processing_thread = threading.Thread(
            target=self._process_loop,
            name="Android-Processor",
            daemon=True,
        )
        self._processing_thread.start()

        self.server.publish_status(is_recording=True)
        logger.info("录音流水线已启动")

    def _on_stop_recording(self):
        if not self._recording:
            return

        logger.info("<<< 停止录音")
        self._recording = False
        self._stop_event.set()

        if self.capture:
            self.capture.stop()
            self.capture = None

        if self._processing_thread and self._processing_thread.is_alive():
            self._processing_thread.join(timeout=3.0)

        self._processing_thread = None
        self.server.publish_status(is_recording=False)

    def _on_clear_results(self):
        self.processor.reset()
        logger.info("结果已清空")

    # ── 核心处理循环 ──────────────────────────────────

    def _process_loop(self):
        """音频采集 → 发送推理 → 显示结果。"""
        logger.info("处理线程已启动")

        while self._recording and not self._stop_event.is_set():
            try:
                chunk = self._safe_get_chunk()
                if chunk is None:
                    continue

                # VAD 预处理
                processed = self.processor.process(chunk)
                self.server.publish_status(level=processed.level_db)

                if not processed.has_speech:
                    continue

                # 发送到推理服务器
                text = self._transcribe(processed.audio)
                if text:
                    self.server.publish_translation(
                        text=text,
                        segment_id=processed.segment_id,
                        is_interim=processed.is_segment_end is False,
                    )

            except Exception as e:
                logger.error(f"处理循环异常: {e}")
                time.sleep(0.1)

        logger.info("处理线程已退出")

    def _transcribe(self, wav_bytes: bytes) -> Optional[str]:
        """发送音频到推理服务器，返回识别文本。"""
        try:
            import urllib.request
            import json

            # Base64 编码 WAV 数据
            wav_b64 = base64.b64encode(wav_bytes).decode()

            # POST 到推理服务器
            req_data = json.dumps({"wav": wav_b64}).encode()
            req = urllib.request.Request(
                f"{self.inference_url}/transcribe",
                data=req_data,
                headers={"Content-Type": "application/json"},
            )

            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                text = result.get("text", "")
                return text.strip() or None

        except Exception as e:
            logger.warning(f"推理请求失败: {e}")
            return None

    def _check_inference_server(self) -> bool:
        """检查推理服务器是否在线。"""
        try:
            import urllib.request
            import json

            req = urllib.request.Request(f"{self.inference_url}/health")
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
                return data.get("status") == "ok"
        except Exception:
            return False

    def _safe_get_chunk(self, timeout: float = 0.5) -> Optional[bytes]:
        try:
            if self.capture and self.capture.is_running:
                return self.capture.get_chunk(timeout=timeout)
        except Exception as e:
            logger.warning(f"获取音频块失败: {e}")
        return None

    @property
    def is_recording(self) -> bool:
        return self._recording


# ── 入口 ──────────────────────────────────────────────
def main():
    import sys

    logger.info("=" * 50)
    logger.info("粤语实时翻译 - Android 版")
    logger.info(f"推理服务器: {INFERENCE_URL}")
    logger.info("=" * 50)

    transcriber = AndroidTranscriber(
        host="127.0.0.1",
        port=5000,
        inference_url=INFERENCE_URL,
    )

    # 注册信号处理（Android 上 signal 支持不完整，忽略异常）
    try:
        import signal
        def handle_signal(sig, frame):
            logger.info("正在停止服务...")
            if transcriber.is_recording:
                transcriber._on_stop_recording()
            sys.exit(0)
        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)
    except Exception:
        pass

    transcriber.run()


if __name__ == "__main__":
    try:
        # 记录启动信息
        logger.info("APK 启动，Python: %s, 平台: %s", sys.version, sys.platform)
        main()
    except Exception as e:
        # 兜底：任何未捕获异常都写入崩溃日志
        _write_crash_log(type(e), e, e.__traceback__)
        # 尝试保持进程存活 5 秒，让日志写入完成
        time.sleep(5)
