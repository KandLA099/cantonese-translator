"""
web_server.py — WebSocket 实时推送服务
========================================

基于 Flask + Flask-SocketIO 实现的本地 Web 服务器，
将 ASR 识别结果实时推送到手机浏览器。

架构:
  1. main.py 启动录音和 ASR 流水线
  2. web_server.py 作为独立组件，通过 publish_* 方法接收结果
  3. 客户端浏览器通过 WebSocket 接收实时翻译文字
  4. 控制信令（开始/停止/清空）通过 WebSocket 事件处理

使用::

    server = TranslationServer()
    server.on_start_recording = lambda: print("开始录音")
    server.on_stop_recording = lambda: print("停止录音")
    server.run()

    # 从流水线中推送结果
    server.publish_translation(text="今日天气很好")
    server.publish_status(is_recording=True, level=-18.5)
"""

import logging
from typing import Optional, Callable

from flask import Flask, render_template
from flask_socketio import SocketIO, emit

logger = logging.getLogger(__name__)


class TranslationServer:
    """
    翻译结果 WebSocket 推流服务器。

    Args:
        host: 监听地址 (默认 127.0.0.1)
        port: 监听端口 (默认 5000)
        debug: 是否开启 Flask 调试模式
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5000,
        debug: bool = False,
    ):
        self.host = host
        self.port = port
        self.debug = debug

        # Flask 应用
        self.app = Flask(
            __name__,
            template_folder="templates",
            static_folder="static",
            static_url_path="/static",
        )
        self.app.config["SECRET_KEY"] = "cantonese-translator-secret-key"
        self.app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0  # 开发时禁用缓存

        # SocketIO 实例（threading 模式兼容性好）
        self.socketio = SocketIO(
            self.app,
            cors_allowed_origins="*",
            async_mode="threading",
            logger=debug,
            engineio_logger=debug,
            ping_timeout=30,
            ping_interval=10,
        )

        # 外部回调（由 main.py 注册）
        self.on_start_recording: Optional[Callable] = None
        self.on_stop_recording: Optional[Callable] = None
        self.on_clear_results: Optional[Callable] = None

        # 连接客户端计数
        self._client_count = 0

        # 注册路由和事件
        self._setup_routes()
        self._setup_socketio_events()

    # ----------------------------------------------------------
    # 路由
    # ----------------------------------------------------------

    def _setup_routes(self):
        @self.app.route("/")
        def index():
            return render_template("index.html")

        @self.app.route("/health")
        def health():
            return {"status": "ok", "clients": self._client_count}

    # ----------------------------------------------------------
    # WebSocket 事件
    # ----------------------------------------------------------

    def _setup_socketio_events(self):
        @self.socketio.on("connect")
        def handle_connect():
            self._client_count += 1
            logger.info(f"客户端已连接 (共 {self._client_count})")
            emit("status_update", {
                "message": "connected",
                "clients": self._client_count,
            })

        @self.socketio.on("disconnect")
        def handle_disconnect():
            self._client_count = max(0, self._client_count - 1)
            logger.info(f"客户端已断开 (共 {self._client_count})")

        @self.socketio.on("start_recording")
        def handle_start_recording():
            logger.info("客户端请求: 开始录音")
            if self.on_start_recording:
                self.on_start_recording()
            else:
                logger.warning("on_start_recording 回调未注册")

        @self.socketio.on("stop_recording")
        def handle_stop_recording():
            logger.info("客户端请求: 停止录音")
            if self.on_stop_recording:
                self.on_stop_recording()
            else:
                logger.warning("on_stop_recording 回调未注册")

        @self.socketio.on("clear_results")
        def handle_clear():
            logger.info("客户端请求: 清空结果")
            if self.on_clear_results:
                self.on_clear_results()

    # ----------------------------------------------------------
    # 推送方法（供 main.py 调用）
    # ----------------------------------------------------------

    def publish_translation(
        self,
        text: str,
        segment_id: int = 0,
        is_interim: bool = False,
    ):
        """
        推送一条翻译结果到所有客户端。

        Args:
            text: 识别出的文字
            segment_id: 语音段 ID（用于区分不同发言）
            is_interim: 是否为临时结果（后续可能修正）
        """
        self.socketio.emit("translation_result", {
            "text": text,
            "segment_id": segment_id,
            "is_interim": is_interim,
        })

    def publish_status(
        self,
        is_recording: Optional[bool] = None,
        level: Optional[float] = None,
        elapsed: Optional[float] = None,
        error: Optional[str] = None,
    ):
        """
        推送状态更新到所有客户端。

        Args:
            is_recording: 是否正在录音
            level: 当前音频电平 (dBFS)
            elapsed: 已录音时长 (秒)
            error: 错误信息（如有）
        """
        data = {}
        if is_recording is not None:
            data["is_recording"] = is_recording
        if level is not None:
            data["level"] = round(level, 1)
        if elapsed is not None:
            data["elapsed"] = round(elapsed, 1)
        if error is not None:
            data["error"] = error

        if data:
            self.socketio.emit("status_update", data)

    # ----------------------------------------------------------
    # 启动
    # ----------------------------------------------------------

    @property
    def url(self) -> str:
        """返回可访问的 URL。"""
        return f"http://{self.host}:{self.port}"

    def run(self):
        """启动 Web 服务器（阻塞）。"""
        logger.info(f"📍 打开手机 Chrome 访问: {self.url}")
        logger.info(f"   按 Ctrl+C 停止服务")
        self.socketio.run(
            self.app,
            host=self.host,
            port=self.port,
            debug=self.debug,
            allow_unsafe_werkzeug=True,
        )


# ============================================================
# 独立运行测试
# ============================================================
if __name__ == "__main__":
    import sys
    import time

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    print("=" * 50)
    print("粤语实时翻译 - Web 服务器测试")
    print("=" * 50)

    # 模拟 ASR 结果推送
    def simulate_asr():
        import threading

        test_texts = [
            "今日天气几好",
            "今日天气几好啊",
            "今日天气几好，出街记得带遮",
            "听日可能会有雨",
            "听日可能会有雨，温度会下降",
        ]

        def push():
            time.sleep(3)  # 等客户端连上
            for i, text in enumerate(test_texts):
                if not hasattr(simulate_asr, "_running") or not simulate_asr._running:
                    break
                server.publish_translation(
                    text=text,
                    segment_id=i // 2,
                    is_interim=False,
                )
                server.publish_status(
                    is_recording=True,
                    level=-15 + i * 2,
                    elapsed=i * 1.5,
                )
                time.sleep(1.5)
            server.publish_status(is_recording=False)

        t = threading.Thread(target=push, daemon=True)
        simulate_asr._running = True
        t.start()

    server = TranslationServer(debug=True)
    print(f"\n打开浏览器访问: {server.url}")
    print("模拟 ASR 将在 3 秒后开始推送测试结果...\n")

    try:
        simulate_asr()
        server.run()
    except KeyboardInterrupt:
        print("\n服务器已停止。")
        simulate_asr._running = False
