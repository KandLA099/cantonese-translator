#!/usr/bin/env python3
"""
main.py — 粤语实时翻译 主入口
================================

整合所有模块的主控制器:
  1. 加载 ASR 模型 (SenseVoiceEngine)
  2. 启动 Web 服务器 (TranslationServer)
  3. 通过 WebSocket 控制信令启动/停止录音流水线
  4. 后台处理线程: 音频采集 → VAD 检测 → ASR 推理 → 结果推送

架构::

    ┌─────────────────────────────────────────────────────┐
    │  main.py (Transcriber)                              │
    │                                                     │
    │  用户点击「开始」                                    │
    │       │                                              │
    │       ▼                                              │
    │  ┌──────────┐   ┌──────────────┐   ┌────────────┐  │
    │  │ Audio    │→→→│ Audio        │→→→│ ASR        │  │
    │  │ Capture  │   │ Processor    │   │ Engine     │  │
    │  │ (逐秒采) │   │ (VAD过滤)    │   │ (ONNX推理) │  │
    │  └──────────┘   └──────────────┘   └────────────┘  │
    │       │                                              │
    │       ▼                                              │
    │  ┌────────────────────────────────────────────────┐  │
    │  │        TranslationServer (WebSocket推送)        │  │
    │  │        └──→ 手机浏览器显示实时翻译结果            │  │
    │  └────────────────────────────────────────────────┘  │
    └─────────────────────────────────────────────────────┘

使用::

    # 正常启动
    python main.py

    # 指定参数
    python main.py --model-dir models --threads 2 --host 0.0.0.0 --port 5000
"""

import argparse
import logging
import signal
import sys
import threading
import time
from typing import Optional

# 根据平台选择音频采集实现
try:
    from android_audio import AndroidAudioCapture, is_android, get_audio_capture
    _has_android = True
except ImportError:
    _has_android = False
    AndroidAudioCapture = None
    is_android = lambda: False
    get_audio_capture = None

from audio_capture import AudioCapture
from audio_processor import AudioProcessor
from asr_engine import SenseVoiceEngine, ModelLoadError
from web_server import TranslationServer

logger = logging.getLogger("main")


class Transcriber:
    """
    主控制器，协调音频采集、VAD、ASR、结果推送全流程。

    通过 TranslationServer 注册 WebSocket 回调，
    响应用户的「开始录音」「停止录音」「清空」操作。
    """

    def __init__(
        self,
        model_dir: str = "models",
        num_threads: int = 2,
        host: str = "127.0.0.1",
        port: int = 5000,
        debug: bool = False,
    ):
        # 组件
        self.capture: Optional[AudioCapture] = None
        self.processor = AudioProcessor()
        self.engine: Optional[SenseVoiceEngine] = None
        self.server = TranslationServer(host=host, port=port, debug=debug)

        # 配置
        self.model_dir = model_dir
        self.num_threads = num_threads

        # 线程状态
        self._recording = False
        self._processing_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # 注册 WebSocket 回调
        self.server.on_start_recording = self._on_start_recording
        self.server.on_stop_recording = self._on_stop_recording
        self.server.on_clear_results = self._on_clear_results

    # ----------------------------------------------------------
    # 生命周期
    # ----------------------------------------------------------

    def load_model(self):
        """加载 ASR 模型。"""
        logger.info("加载 ASR 模型中...")
        self.engine = SenseVoiceEngine(
            model_dir=self.model_dir,
            num_threads=self.num_threads,
        )
        self.engine.load()
        logger.info(f"ASR 模型加载完成: {self.engine.stats}")

    def run(self):
        """启动 Web 服务器（阻塞）。"""
        print()
        print("=" * 54)
        print("  粤语实时翻译 v1.0")
        print("  Cantonese Real-Time Translator")
        print("=" * 54)
        print()

        if self.engine and self.engine.is_loaded:
            print(f"  ✓ ASR 模型已加载")
            print(f"  ✓ 线程数: {self.num_threads}")
        else:
            print(f"  ⚠ ASR 模型未加载，请先运行:")
            print(f"     cd models && bash download_model.sh")

        print()
        print(f"  📍 打开手机 Chrome 访问:")
        print(f"     {self.server.url}")
        print()
        print(f"  ⏹  Ctrl+C 停止服务")
        print()

        self.server.run()

    # ----------------------------------------------------------
    # WebSocket 回调
    # ----------------------------------------------------------

    def _on_start_recording(self):
        """用户点击「开始录音」。"""
        if self._recording:
            logger.warning("已在录音中，忽略重复请求")
            return

        if not self.engine or not self.engine.is_loaded:
            self.server.publish_status(error="模型未加载，无法开始录音")
            logger.error("模型未加载")
            return

        logger.info(">>> 开始录音")
        self._recording = True
        self._stop_event.clear()

        # 重置处理器状态
        self.processor.reset()

        # 启动音频采集（根据平台选择实现）
        if _has_android and is_android():
            logger.info("使用 Android AudioRecord 采集")
            self.capture = AndroidAudioCapture()
        else:
            logger.info("使用 Termux 麦克风采集")
            self.capture = AudioCapture()
        self.capture.start()

        # 启动后台处理线程
        self._processing_thread = threading.Thread(
            target=self._process_loop,
            name="ASR-Processor",
            daemon=True,
        )
        self._processing_thread.start()

        self.server.publish_status(is_recording=True)
        logger.info("录音流水线已启动")

    def _on_stop_recording(self):
        """用户点击「停止录音」。"""
        if not self._recording:
            return

        logger.info("<<< 停止录音")
        self._recording = False
        self._stop_event.set()

        # 停止音频采集
        if self.capture:
            self.capture.stop()
            self.capture = None

        # 等待处理线程结束
        if self._processing_thread and self._processing_thread.is_alive():
            self._processing_thread.join(timeout=5.0)

        self._processing_thread = None
        self.server.publish_status(is_recording=False)
        logger.info("录音流水线已停止")

    def _on_clear_results(self):
        """用户点击「清空」。"""
        self.processor.reset()
        logger.info("结果已清空")

    # ----------------------------------------------------------
    # 后台处理循环
    # ----------------------------------------------------------

    def _process_loop(self):
        """
        核心处理循环（在后台线程中运行）。

        流程:
          1. 从 AudioCapture 获取音频块
          2. AudioProcessor 做 VAD 检测
          3. 有语音 → ASR 推理
          4. 推送结果到前端
          5. 更新电平状态
        """
        logger.info("处理线程已启动")

        while self._recording and not self._stop_event.is_set():
            try:
                # 1. 获取音频块
                chunk = self._safe_get_chunk()
                if chunk is None:
                    continue

                # 2. VAD 检测 + 预处理
                processed = self.processor.process(chunk)

                # 3. 更新电平
                self.server.publish_status(level=processed.level_db)

                # 4. 静音则跳过推理
                if not processed.has_speech:
                    if processed.is_segment_end:
                        logger.debug("语音段结束")
                    continue

                # 5. ASR 推理
                try:
                    text = self.engine.recognize(processed.audio)
                except Exception as e:
                    logger.error(f"ASR 推理失败: {e}", exc_info=True)
                    continue

                if not text or not text.strip():
                    continue

                logger.debug(f"识别: [{processed.segment_id}] {text}")

                # 6. 推送结果到前端
                self.server.publish_translation(
                    text=text,
                    segment_id=processed.segment_id,
                    is_interim=processed.is_segment_end is False,
                )

                # 7. 如果段结束，标记 final
                if processed.is_segment_end:
                    logger.debug(f"段 {processed.segment_id} 结束: {text}")

            except Exception as e:
                logger.error(f"处理循环异常: {e}", exc_info=True)
                # 短暂恢复后继续
                time.sleep(0.1)

        logger.info("处理线程已退出")

    def _safe_get_chunk(self, timeout: float = 0.5) -> Optional[bytes]:
        """安全获取音频块（带超时和异常处理）。"""
        try:
            if self.capture and self.capture.is_running:
                return self.capture.get_chunk(timeout=timeout)
        except Exception as e:
            logger.warning(f"获取音频块失败: {e}")
        return None

    # ----------------------------------------------------------
    # 属性
    # ----------------------------------------------------------

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def stats(self) -> dict:
        """系统状态摘要。"""
        stats = {
            "recording": self._recording,
            "model_loaded": self.engine.is_loaded if self.engine else False,
            "processor": self.processor.stats if self.processor else {},
            "engine": self.engine.stats if self.engine else {},
        }
        return stats


# ============================================================
# CLI 入口
# ============================================================


def parse_args(argv=None):
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="粤语实时翻译工具 - 完全离线 Android 部署",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                          # 默认启动
  python main.py --debug                  # 调试模式
  python main.py --host 0.0.0.0 --port 8080  # 自定义端口
        """,
    )
    parser.add_argument(
        "--model-dir", default="models",
        help="SenseVoice 模型文件目录 (默认: models)",
    )
    parser.add_argument(
        "--threads", type=int, default=2,
        help="ONNX Runtime 推理线程数 (默认: 2，手机端推荐 2-4)",
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="服务器监听地址 (默认: 127.0.0.1)",
    )
    parser.add_argument(
        "--port", type=int, default=5000,
        help="服务器监听端口 (默认: 5000)",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="启用调试日志和 Flask debug 模式",
    )
    return parser.parse_args(argv)


def setup_logging(debug: bool):
    """配置日志。"""
    level = logging.DEBUG if debug else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(message)s" if debug else \
          "%(asctime)s [%(levelname)s] %(message)s"

    logging.basicConfig(
        level=level,
        format=fmt,
        datefmt="%H:%M:%S",
    )

    # 降低第三方库日志噪音
    logging.getLogger("socketio").setLevel(logging.WARNING)
    logging.getLogger("engineio").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)


def main(argv=None):
    """程序入口。"""
    args = parse_args(argv)
    setup_logging(args.debug)

    # 创建主控制器
    transcriber = Transcriber(
        model_dir=args.model_dir,
        num_threads=args.threads,
        host=args.host,
        port=args.port,
        debug=args.debug,
    )

    # 加载 ASR 模型
    try:
        transcriber.load_model()
    except ModelLoadError as e:
        print(f"\n[错误] 模型加载失败: {e}")
        print("请确保已下载模型文件:")
        print(f"  cd {args.model_dir} && bash download_model.sh\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n[错误] 启动失败: {e}\n")
        if args.debug:
            import traceback
            traceback.print_exc()
        sys.exit(1)

    # 注册信号处理（优雅退出）
    def handle_signal(sig, frame):
        print("\n正在停止服务...")
        if transcriber.is_recording:
            transcriber._on_stop_recording()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # 启动（阻塞）
    transcriber.run()


if __name__ == "__main__":
    main()
