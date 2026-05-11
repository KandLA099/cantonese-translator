[app]

# 应用基本信息
title = 粤语实时翻译
package.name = cantonesetranslator
package.domain = com.cantonesetranslator

# 源代码配置
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,html,js,css,json
source.exclude_exts = spec,ide,sh,bat,txt,md
source.exclude_dirs = __pycache__, .git, .vscode, recipes

# 排除桌面版文件（依赖 onnxruntime，APK 中不可用会闪退）
source.exclude = main_desktop.py, main_android.py, asr_engine.py, asr_engine_android.py, inference_server.py

# 应用版本
version = 0.1
version.code = 1

# 依赖（python-for-android 会自动处理这些）
# onnxruntime 没有 Android wheel，推理跑在 Termux 独立进程
requirements = python3, flask, flask-socketio, numpy, pyjnius, werkzeug

# 权限（录音 + 网络 + 本地通信）
android.permissions = RECORD_AUDIO, INTERNET, ACCESS_NETWORK_STATE

# Android API 配置
android.api = 34
android.minapi = 26
android.targetapi = 34
android.ndk = 26b
android.accept_sdk_license = True
android.archs = arm64-v8a
android.enable_androidx = True
android.multidex = False
android.allow_backup = False

# 竖屏显示
orientation = portrait

# Python-for-android 配置
p4a.branch = develop
p4a.allow_presplash = True
p4a.bootstrap = webview

[buildozer]

log_level = 3
warn_on_root = 1
bin_dir = ./bin
build_dir = .buildozer
