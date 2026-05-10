# Android 部署方案
# ==================
#
# ## onnxruntime Android 支持现状
#
# | 方案 | 可行性 | 说明 |
# |------|--------|------|
# | pip install onnxruntime | ❌ | 官方没有 Android wheel |
# | python-for-android recipe | ⚠️ | 有人做出来了但极其复杂 |
# | Microsoft AAR + pyjnius | ⚠️ | 见 asr_engine_android.py，有 JNI 兼容性坑 |
# | 独立推理进程 | ✅ | 最可靠 |
#
# ## 推荐方案：分离架构
#
# 将 APK 拆分为两部分：
#
# 1. **APK (Python-for-Android)**：
#    - 音频采集（android_audio.py，AudioRecord）
#    - Flask-SocketIO Web 服务器（UI + 结果显示）
#    - 通过 WebSocket 与推理进程通信
#
# 2. **推理进程 (Termux 或独立 APK)**：
#    - onnxruntime Python 包
#    - SenseVoice 模型推理
#    - HTTP/WebSocket 返回结果
#
# 通信架构：
#
#   ┌─────────────────────────┐      WebSocket      ┌────────────────────┐
#   │   Android APK           │ ←─────────────────→ │   推理进程           │
#   │   (android_audio.py)    │   ws://localhost   │   (Termux/独立)     │
#   │   + Flask Web UI        │   /socket.io/      │   + onnxruntime     │
#   │   + AudioRecord 采集    │                    │   + SenseVoice      │
#   └─────────────────────────┘                    └────────────────────┘
#
# ## 构建步骤
#
# ### 步骤 1：确保 Docker Desktop 已安装
# https://www.docker.com/products/docker-desktop/
#
# ### 步骤 2：构建 APK
# ```bash
# cd cantonese-translator
# docker run --rm -v "%CD%:/app" kivy/buildozer android debug
# ```
#
# APK 输出: bin/cantonesetranslator-0.1-arm64-v8a-debug.apk
#
# ### 步骤 3：安装并运行
# ```bash
# adb install bin/cantonesetranslator-*.apk
# ```
#
# ### 步骤 4：启动推理进程
# 在 Termux 中：
# ```bash
# pkg install python
# pip install onnxruntime flask flask-socketio
# cd /sdcard/cantonesetranslator
# python inference_server.py
# ```
#
# ## 已知限制
#
# 1. asr_engine_android.py 中的 pyjnius + ONNX Runtime Java API
#    有复杂性，建议使用分离架构（APK + Termux 推理进程）
#
# 2. 如果一定要单 APK，需要 python-for-android 能成功编译 onnxruntime
#    参考: https://github.com/kivy/python-for-android/issues/3216
