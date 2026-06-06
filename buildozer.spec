[app]

# 应用基本信息
title = 粤语实时翻译
package.name = cantonesetranslator
package.domain = com.cantonesetranslator

# 源代码配置
source.dir = .
source.include_exts = py,png,jpg,kv,atlas
source.exclude_exts = spec,ide,sh,bat,txt,md
source.exclude_dirs = __pycache__, .git, .vscode, recipes, templates, static

# 排除桌面版、Web 服务器及废弃文件（不需要打包进 APK）
source.exclude = main_desktop.py, main_android.py, asr_engine.py, asr_engine_android.py, inference_server.py, web_server.py

# 应用版本
version = 0.2.0
version.code = 3

# 依赖（python-for-android 会自动处理这些）
# onnxruntime 没有 Android wheel，推理跑在 Termux 独立进程
requirements = python3, pyjnius, numpy, kivy

# 权限（录音 + 网络 + 存储）
android.permissions = RECORD_AUDIO, INTERNET, ACCESS_NETWORK_STATE, WRITE_EXTERNAL_STORAGE, READ_EXTERNAL_STORAGE, FOREGROUND_SERVICE

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

# 支持竖屏和横屏自适应
orientation = all

# Python-for-android 配置
p4a.branch = develop
p4a.allow_presplash = True
p4a.bootstrap = sdl2

[buildozer]

log_level = 3
warn_on_root = 1
bin_dir = ./bin
build_dir = .buildozer
