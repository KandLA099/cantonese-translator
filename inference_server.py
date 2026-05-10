#!/usr/bin/env python3
"""
inference_server.py — Termux 端 ASR 推理服务器
===============================================

在 Termux 中运行，加载 SenseVoice 模型，通过 WebSocket 提供 ASR 推理服务。
APK 端采集音频后，通过 WebSocket 发送到这里进行推理。

## 启动方式

在 Termux 中执行:

```bash
# 1. 安装依赖
pip install onnxruntime flask flask-socketio numpy scipy

# 2. 下载模型文件（如果还没下载）
bash models/download_model.sh

# 3. 启动推理服务器
python inference_server.py
```

## 通信协议

APK → 推理服务器 (WebSocket 事件):
  - 'audio_chunk': { 'wav': <base64 WAV bytes>, 'segment_id': <int> }
  - 'stop_record': {}

推理服务器 → APK (WebSocket 事件):
  - 'transcription': { 'text': <str>, 'segment_id': <int> }
  - 'status': { 'code': <str>, 'message': <str> }
"""

import argparse
import base64
import logging
import os
import sys
import threading
import time
from typing import Optional

from flask import Flask, request
from flask_socketio import SocketIO, emit

# ── 日志配置 ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("inference_server")


# ── ASR 引擎 ──────────────────────────────────────────────
class ASREngine:
    """
    封装 ASR 推理逻辑。
    """

    def __init__(self, model_dir: str = "models", num_threads: int = 2):
        self.model_dir = model_dir
        self.num_threads = num_threads
        self.engine = None

    def load(self):
        """延迟加载模型。"""
        try:
            from asr_engine import SenseVoiceEngine, ModelLoadError

            self.engine = SenseVoiceEngine(
                model_dir=self.model_dir,
                num_threads=self.num_threads,
            )
            self.engine.load()
            logger.info(f"ASR 模型加载成功: {self.engine.stats}")
        except Exception as e:
            logger.error(f"ASR 模型加载失败: {e}")
            self.engine = None

    def recognize(self, wav_bytes: bytes) -> Optional[str]:
        """识别一段 WAV 音频。"""
        if self.engine is None:
            return None

        try:
            # SenseVoiceEngine.recognize 直接接受 WAV bytes
            text = self.engine.recognize(wav_bytes)
            return text.strip() or None
        except Exception as e:
            logger.error(f"推理失败: {e}")
            return None

    @property
    def is_loaded(self) -> bool:
        return self.engine is not None


# ── WebSocket 服务器 ─────────────────────────────────────
app = Flask(__name__)
app.config["SECRET_KEY"] = "cantonese-inference-server"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# 全局推理引擎
asr: ASREngine = None


@app.route("/health")
def health():
    """健康检查端点。"""
    return {
        "status": "ok",
        "model_loaded": asr.is_loaded if asr else False,
    }


@app.route("/transcribe", methods=["POST"])
def transcribe_http():
    """
    HTTP POST 端点：接收音频 WAV 数据推理（供 APK 调用）。

    请求: POST /transcribe
    Body: { "wav": "<base64 编码的 WAV bytes>" }

    返回: { "text": "<识别文本>", "success": True/False }
    """
    from flask import request, jsonify

    data = request.get_json(silent=True)
    if not data or "wav" not in data:
        return jsonify({"text": "", "success": False}), 400

    try:
        wav_bytes = base64.b64decode(data["wav"])
        text = asr.recognize(wav_bytes) if asr else None
        return jsonify({
            "text": text or "",
            "success": text is not None,
        })
    except Exception as e:
        logger.error(f"HTTP 推理失败: {e}")
        return jsonify({"text": "", "success": False}), 500


@socketio.on("connect")
def on_connect():
    logger.info(f"客户端已连接 (SID: {request.sid})")


@socketio.on("disconnect")
def on_disconnect():
    logger.info(f"客户端已断开 (SID: {request.sid})")


@socketio.on("transcribe")
def on_transcribe(data):
    """
    接收音频 WAV 数据进行推理。

    数据格式:
        { 'wav': <base64 编码的 WAV bytes> }

    返回:
        { 'text': <识别文本>, 'success': True/False }
    """
    sid = request.sid
    if not data or "wav" not in data:
        emit("transcription", {"text": "", "success": False}, to=sid)
        return

    try:
        wav_b64 = data["wav"]
        wav_bytes = base64.b64decode(wav_b64)

        text = asr.recognize(wav_bytes) if asr else None

        if text:
            emit("transcription", {"text": text, "success": True}, to=sid)
        else:
            emit("transcription", {"text": "", "success": True}, to=sid)

    except Exception as e:
        logger.error(f"推理请求处理失败: {e}")
        emit("transcription", {"text": "", "success": False}, to=sid)


# ── 入口 ──────────────────────────────────────────────────
def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="粤语实时翻译 - 推理服务器 (Termux)")
    parser.add_argument("--model-dir", default="models")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5001,
                        help="推理服务器端口 (默认 5001)")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--no-wait", action="store_true",
                        help="不等待模型加载，启动后立即接受连接")
    return parser.parse_args(argv)


def main(argv=None):
    global asr

    args = parse_args(argv)
    level = logging.DEBUG if args.debug else logging.INFO
    logging.getLogger().setLevel(level)

    print("=" * 50)
    print("  粤语实时翻译 - 推理服务器")
    print(f"  端口: {args.port}")
    print("=" * 50)

    # 创建推理引擎
    asr = ASREngine(model_dir=args.model_dir, num_threads=args.threads)

    # 后台加载模型（不阻塞服务器启动）
    def _load_model():
        logger.info("后台加载 ASR 模型...")
        asr.load()
        if asr.is_loaded:
            logger.info("模型加载完成，可以开始推理")
        else:
            logger.warning("模型加载失败，推理功能不可用")
            logger.warning("检查: 1) models/ 目录是否有模型文件")
            logger.warning("      2) pip install onnxruntime 是否成功")

    if args.no_wait:
        # 不等待，直接接受连接
        threading.Thread(target=_load_model, daemon=True).start()
    else:
        # 等待模型加载完成后再启动服务器
        logger.info("正在加载 ASR 模型（首次加载约 5-10 秒）...")
        asr.load()

    # 启动服务器
    print()
    print(f"  📍 推理服务器已启动: ws://{args.host}:{args.port}")
    print(f"  📍 APK 会自动连接到此地址")
    print(f"  ⏹  Ctrl+C 停止服务")
    print()

    socketio.run(
        app,
        host=args.host,
        port=args.port,
        debug=args.debug,
        allow_unsafe_werkzeug=True,
    )


if __name__ == "__main__":
    main()
