# 🎙️ 粤语实时翻译 — Cantonese Real-Time Translator

> 完全离线、在 Android 手机上运行的粤语实时语音识别翻译工具。  
> 对着手机说粤语，屏幕上同步显示普通话文字。**边说话边出，不等你说完。**

---

## 效果

| 你说（粤语） | 屏幕显示（普通话） |
|--------------|-------------------|
| 今日天气几好 | 今日天气很好 |
| 听日可能会有雨 | 明天可能会有雨 |
| 我哋几点出发 | 我们几点出发 |

**实时性**: 每 0.5 秒更新一次翻译结果，语音不停显示不停。

---

## 目录

1. [准备工作](#1-准备工作)
2. [安装步骤](#2-安装步骤)
3. [使用说明](#3-使用说明)
4. [界面说明](#4-界面说明)
5. [常见问题](#5-常见问题)
6. [技术架构](#6-技术架构)
7. [文件说明](#7-文件说明)

---

## 1. 准备工作

### 需要的设备和环境

| 项目 | 要求 |
|------|------|
| **手机** | Android 10+（你的是 Oppo Find X8 / ColorOS 16，完全没问题） |
| **WiFi** | 同一网络下（手机和电脑在同一 WiFi）_* 可选，纯手机使用不需要 |
| **数据线** | 用于传输文件（或通过微信/QQ发送文件到手机） |

### 需要的 APK（先下载好）

从 **F-Droid** 下载这两个 APK（不要从 Play Store 下载，Play 版已停更）：

| APK | 下载地址 | 用途 |
|-----|----------|------|
| **Termux** | F-Droid 搜索 "Termux" | 提供 Android 上的 Linux 终端环境 |
| **Termux:API** | F-Droid 搜索 "Termux:API" | 提供麦克风等手机硬件访问权限 |

> 💡 **如何安装 F-Droid**: 手机浏览器打开 `https://f-droid.org`，下载 F-Droid APK 安装后，在里面搜索安装上述两个应用。

---

## 2. 安装步骤

### 第一步：复制项目文件到手机

将本项目的 `cantonese-translator` 文件夹（所有文件）复制到手机存储中，建议路径:

```
内部存储/Download/cantonese-translator/
```

### 第二步：安装 Termux 和 Termux:API

1. 安装 F-Droid → 搜索 Termux → 安装
2. 搜索 Termux:API → 安装
3. **打开手机「设置 → 应用 → Termux → 权限」**，开启**麦克风权限**

### 第三步：在 Termux 中运行安装

打开 Termux，依次执行:

```bash
# 1. 授予 Termux 文件访问权限
termux-setup-storage
# → 手机会弹出权限请求，点击「允许」

# 2. 进入项目目录（假设你放在 Download 里）
cd /sdcard/Download/cantonese-translator

# 3. 一键安装（系统依赖 + Python包 + 下载模型）
chmod +x setup.sh
./setup.sh
```

> ⏳ **安装耗时**: 约 5-15 分钟（取决于网络速度，模型 ~200MB 需要下载）
>
> 如果 `./setup.sh` 中间卡住或报错，可以跳到第四步手动安装。

### 第四步：单独手动安装（备用方案）

如果 `setup.sh` 自动安装失败，按以下步骤手动操作:

```bash
# 1. 更新软件源
pkg update -y && pkg upgrade -y

# 2. 安装系统包
pkg install -y python build-essential ffmpeg termux-api wget cmake

# 3. 安装 Python 依赖
pip install --upgrade pip

# 先装 onnxruntime（可能需要较长时间）
pip install onnxruntime

# 再装其余
pip install flask flask-socketio numpy soundfile webrtcvad scipy

# 4. 下载模型
cd models
chmod +x download_model.sh
./download_model.sh
cd ..

# 5. 测试安装
python -c "import onnxruntime; print('ONNX:', onnxruntime.__version__)"
python -c "import flask; print('Flask:', flask.__version__)"
```

### 第五步：验证模型文件

确保 `models/` 目录下有以下文件（约 200MB）:

```
models/
├── model.onnx        ← SenseVoice 模型本体
├── config.yaml       ← 模型配置
├── tokens.txt        ← 词汇表
├── am.mvn            ← 均值方差归一化参数
└── se_dict.txt       ← 情感识别字典（可选）
```

---

## 3. 使用说明

### 启动

每次使用时，在 Termux 中执行:

```bash
cd /sdcard/Download/cantonese-translator
chmod +x run.sh
./run.sh
```

启动成功后你会看到:

```
======================================================
   粤语实时翻译 - Cantonese Real-Time Translator
======================================================

  ✅ 服务已启动！

  打开手机 Chrome，访问:
  http://127.0.0.1:5000

  快捷键:
    空格键 = 开始/停止录音
    Esc    = 清空结果
    Ctrl+C = 退出程序

  确保已授予 Termux 麦克风权限！
```

### 打开翻译界面

1. 打开手机上的 **Chrome 浏览器**
2. 在地址栏输入: `http://127.0.0.1:5000`
3. 点击 **全屏图标** 或把浏览器横屏以获得最佳体验

### 操作

| 操作 | 方式 |
|------|------|
| **开始录音** | 点击底部 🎤 绿色按钮（或按键盘空格键） |
| **停止录音** | 再点一次红色按钮（或按空格键） |
| **清空结果** | 点击 🗑️ 垃圾桶图标（或按 Esc） |
| **退出程序** | 在 Termux 中按 Ctrl+C |

### 翻译效果

1. 点击「开始」按钮 → 绿色指示灯亮起 → 开始计时
2. 对着手机麦克风说粤语
3. 屏幕上实时弹出普通话文字（每 0.5 秒更新一次）
4. 停止说话后，最后一行文字从灰色变为白色（确认态）
5. 点击「停止」结束录音
6. 点击「清空」开始新一轮翻译

---

## 4. 界面说明

```
┌────────────────────────────────────────┐
│ ● 录音中              00:23       ▃▇▆ │  ← 状态栏
├────────────────────────────────────────┤  · 指示灯: 绿=录音中 / 灰=已停止
│                                        │  · 计时器: 录音时长
│  今日天气几好，出街记得带遮              │  · 电平表: 声音大小
│                                        │
│  ──── 段 2 ────                        │  ← 语音段分隔线
│                                        │     (停顿1秒以上自动切段)
│  听日可能会有雨，温度会下降              │
│                                        │  ← 翻译结果（大号白字）
│  我们几点出发                          │
│                                        │    灰色斜体=正在识别
│                                        │    白色正体=已确认
│                                        │
├────────────────────────────────────────┤
│  🗑️         🎤 停止                ⚙️   │  ← 控制栏
└────────────────────────────────────────┘    清空  开始/停止  设置
```

---

## 5. 常见问题

### Q: Termux 启动后显示 "termux-microphone-record: command not found"

**原因**: 未安装 Termux:API 或未安装 termux-api 包

**解决**:
```bash
pkg install termux-api
```
同时检查手机「设置 → 应用 → Termux → 权限 → 麦克风」是否已开启。

### Q: 打开浏览器显示 "无法访问此网站"

**原因**: 服务未启动或端口不对

**解决**:
1. 确认 Termux 已运行 `./run.sh` 且无报错
2. 确认 URL 输入正确: `http://127.0.0.1:5000`（不是 https，不是别的端口）
3. 尝试刷新页面

### Q: 翻译结果不准确或没反应

**原因**: 可能的原因较多:

1. **模型未下载**: 检查 `models/model.onnx` 是否存在
2. **麦克风权限**: 检查手机设置
3. **环境噪音太大**: 尽量靠近麦克风说话
4. **说的不是粤语**: 当前模型只支持粤语→普通话

### Q: 运行时报 "onnxruntime 未安装"

**原因**: onnxruntime 在 Termux 上安装可能失败

**解决**:
```bash
# 试试安装社区版
pkg install onnxruntime

# 或者用纯 CPU 版本
pip install onnxruntime --no-deps

# 如果还不行，试试阿里镜像
pip install onnxruntime -i https://mirrors.aliyun.com/pypi/simple/
```

### Q: 识别太慢，跟不上说话速度

**原因**: 手机 CPU 推理速度有限

**解决**:
- 在 Termux 中按 Ctrl+C 停止
- 重新运行: `python main.py --threads 4`（用 4 个线程推理）
- 如果还是慢，可以改为每 1 秒推理一次（修改 `audio_capture.py` 中的 `DEFAULT_HOP_DURATION = 1.0`）

### Q: 怎么在电脑上测试？

在电脑上安装 Python 运行同样代码即可（不需要 Termux），但麦克风采集会使用系统默认设备:

```bash
# 电脑上
pip install -r requirements.txt
python main.py --host 0.0.0.0
# 然后在手机 Chrome 访问 http://电脑IP:5000
```

### Q: 手机存储空间不够？

模型文件约 200MB，项目总大小约 210MB。可以在用完后删除 `models/` 目录释放空间，下次用前重新下载。

---

## 6. 技术架构

```
┌───────────────────────────────────────────────────────────┐
│                      手机 (Oppo Find X8)                   │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐  │
│  │              Termux (Android Linux 环境)              │  │
│  │                                                       │  │
│  │  Python main.py                                       │  │
│  │    ┌──────────────┐   ┌─────────────┐   ┌─────────┐  │  │
│  │    │ AudioCapture │──▶│ AudioProc   │──▶│ ASR     │  │  │
│  │    │ (麦克风采集)  │   │ (VAD检测)   │   │ Engine  │  │  │
│  │    └──────────────┘   └─────────────┘   └────┬────┘  │  │
│  │                                               │       │  │
│  │                                               ▼       │  │
│  │                                        ┌───────────┐  │  │
│  │                                        │ WebServer │  │  │
│  │                                        │ SocketIO  │  │  │
│  │                                        └─────┬─────┘  │  │
│  └─────────────────────────────────────────────────│─────┘  │
│                                                      │       │
│  ┌───────────────────────────────────────────────────┘       │
│  │  WebSocket (ws://127.0.0.1:5000)                          │
│  ▼                                                           │
│  ┌─────────────────────────────────────────────────────┐     │
│  │          手机 Chrome 浏览器 (http://localhost:5000)   │     │
│  │  ┌──────────────────────────────────────────────┐   │     │
│  │  │  深色全屏界面 · 实时翻译结果 · 电平表 · 计时器  │   │     │
│  │  └──────────────────────────────────────────────┘   │     │
│  └─────────────────────────────────────────────────────┘     │
└───────────────────────────────────────────────────────────┘
```

### 核心模型

| 模型 | 说明 |
|------|------|
| **SenseVoice Small** | 阿里达摩院开源，ONNX 格式 ~200MB |
| 语言支持 | 粤语 → 普通话简体中文（直接输出，无需额外翻译） |
| 音频格式 | 16kHz / 16-bit / 单声道 / PCM WAV |

### 实时流式处理

- 音频以 **1 秒窗口** 切割，**0.5 秒步进**（50% 重叠）
- 每 0.5 秒对新窗口做一次 ASR 推理
- 重叠窗口产生的结果在前端自动**去重合并**，保证显示流畅

---

## 7. 文件说明

```
cantonese-translator/
│
├── run.sh                  ← 🚀 一键启动（日常使用入口）
├── setup.sh                ← 📦 一键安装（首次部署用）
├── requirements.txt        ← Python 依赖清单
├── main.py                 ← 🧠 主控制器（整合所有模块）
├── README.md               ← 📖 本说明文档
│
├── audio_capture.py        ← 🎤 音频采集模块
│                            · 基于 termux-microphone-record
│                            · 滑动窗口 1s/0.5s 连续录音
│
├── audio_processor.py      ← 🔍 音频预处理 + VAD 检测
│                            · webrtcvad 语音活动检测
│                            · 语音段追踪（切分/结束标记）
│                            · 音频电平计算
│
├── asr_engine.py           ← 🧩 ASR 推理引擎
│                            · 加载 SenseVoice ONNX 模型
│                            · 音频预处理 → 推理 → token 解码
│                            · 自动适配模型输入输出接口
│
├── web_server.py           ← 🌐 WebSocket 推流服务器
│                            · Flask + Flask-SocketIO
│                            · 控制信令（开始/停止/清空）
│                            · 实时结果推送
│
├── models/
│   └── download_model.sh   ← ↓ 从 ModelScope 下载模型
│
├── templates/
│   └── index.html          ← 前端 HTML 页面
│
└── static/
    ├── style.css           ← 深色全屏主题样式
    └── app.js              ← WebSocket 客户端 + UI 交互
```

---

## 许可证

本项目仅供个人学习使用。SenseVoice 模型版权归阿里巴巴达摩院所有，请遵守其开源许可协议。

## 致谢

- [SenseVoice](https://github.com/FunAudioLLM/SenseVoice) — 阿里达摩院多语种语音识别模型
- [Termux](https://termux.dev/) — Android 终端模拟器
- [ONNX Runtime](https://onnxruntime.ai/) — 跨平台推理引擎
- [webrtcvad](https://github.com/wiseman/py-webrtcvad) — WebRTC 语音活动检测
