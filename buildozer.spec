[app]

# 应用基本信息
title = 粤语实时翻译
package.name = cantonesetranslator
package.domain = com.cantonesetranslator

# 源代码配置
source.dir = .
source.include_exts = py,png,jpg,html,js,css,kv,atlas
source.exclude_exts = spec,ide,sh,bat,txt,md
source.exclude_dirs = __pycache__, .git, .vscode, recipes, templates, static

# 排除不需要打包的文件
source.exclude = main_desktop.py, main_android.py, main_kivy_bak.py, asr_engine.py, asr_engine_android.py, audio_capture.py, audio_processor.py, inference_server.py, web_server.py, history.py, settings.py, utils.py, test_qa_validation.py, QA_REPORT.md

# 应用版本
version = 0.3.0
version.code = 4

# 依赖（极简：webview bootstrap 不需要 Kivy）
# 前端用 HTML/JS，Python 只用标准库 http.server + pyjnius 录音
requirements = python3, pyjnius, numpy

# 权限（录音 + 网络）
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

# 支持竖屏和横屏自适应
orientation = all

# Python-for-android 配置
p4a.branch = develop
p4a.allow_presplash = True
p4a.bootstrap = webview
p4a.webview_url = http://localhost:8080

[buildozer]

log_level = 3
warn_on_root = 1
bin_dir = ./bin
build_dir = .buildozer
