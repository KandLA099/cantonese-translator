"""
android_audio.py — Android 原生音频采集模块
============================================

基于 pyjnius 调用 Android AudioRecord API 实现实时音频采集。
替代 termux-microphone-record，用于 Buildozer 打包后的独立 APK。

接口与 audio_capture.AudioCapture 完全兼容：
  - start() / stop()
  - get_chunk(timeout) -> Optional[bytes]
  - stream(timeout) -> generator
  - is_running, audio_format
  - 支持上下文管理器 (with ... as ...)

音频格式: 16kHz, 16-bit, 单声道, PCM，输出包装为完整 WAV 格式。

依赖:
  - pyjnius (Python-Java 桥接)
  - Android RECORD_AUDIO 权限 (在 buildozer.spec 中声明)

原理:
  1. 通过 pyjnius 调用 Android AudioRecord API
  2. 后台线程持续读取 PCM 数据到缓冲区
  3. 用滑动窗口切割成 1 秒/0.5 秒步进的音频块
  4. 每块重新打包成标准 WAV，放入线程安全队列
"""

import os
import struct
import threading
import time
import queue
from typing import Optional, BinaryIO

# ============================================================
# 常量
# ============================================================
DEFAULT_SAMPLE_RATE = 16000   # 采样率 16kHz
DEFAULT_CHUNK_DURATION = 1.0  # 每个识别窗口 1 秒
DEFAULT_HOP_DURATION = 0.5   # 滑动步进 0.5 秒（50% 重叠）
BYTES_PER_SAMPLE = 2           # 16-bit = 2 bytes


# ============================================================
# 异常类
# ============================================================

class AudioCaptureError(Exception):
    """音频采集相关错误的基类。"""
    pass


class MicrophonePermissionError(AudioCaptureError):
    """未授予麦克风权限。"""
    pass


class JniusNotAvailableError(AudioCaptureError):
    """pyjnius 不可用（非 Android 环境）。"""
    pass


class AudioRecordInitError(AudioCaptureError):
    """AudioRecord 初始化失败。"""
    pass


# ============================================================
# 检测环境
# ============================================================

def is_android() -> bool:
    """
    检测当前是否在 Android 环境运行。

    判断依据（按优先级）:
      1. ANDROID_ARGUMENT 环境变量（Buildozer 设置）
      2. KIVY_ANDROID_LAUNCHER 环境变量（旧版 Kivy）
      3. platform.system() == "Linux" 且存在 /system/build.prop
    """
    if os.environ.get("ANDROID_ARGUMENT"):
        return True
    if os.environ.get("KIVY_ANDROID_LAUNCHER"):
        return True
    try:
        import platform
        if platform.system() == "Linux" and os.path.exists("/system/build.prop"):
            return True
    except Exception:
        pass
    return False


def check_jnius() -> bool:
    """检查 pyjnius 是否可用。"""
    try:
        import jnius  # noqa: F401
        return True
    except ImportError:
        return False


# ============================================================
# AndroidAudioCapture 主类
# ============================================================

class AndroidAudioCapture:
    """
    Android 原生音频采集器（基于 AudioRecord）。

    用法::

        capture = AndroidAudioCapture()
        capture.start()

        while capture.is_running:
            chunk_wav = capture.get_chunk(timeout=1.0)
            if chunk_wav:
                process_chunk(chunk_wav)

        capture.stop()

    也支持作为上下文管理器使用::

        with AndroidAudioCapture() as capture:
            for chunk in capture.stream():
                process_chunk(chunk)
    """

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        chunk_duration: float = DEFAULT_CHUNK_DURATION,
        hop_duration: float = DEFAULT_HOP_DURATION,
    ):
        self.sample_rate = sample_rate
        self.chunk_duration = chunk_duration
        self.hop_duration = hop_duration

        # 计算字节尺寸
        self._bytes_per_sec = sample_rate * BYTES_PER_SAMPLE
        self._chunk_bytes = int(chunk_duration * self._bytes_per_sec)
        self._hop_bytes = int(hop_duration * self._bytes_per_sec)

        # 内部状态
        self._running = False
        self._recorder = None          # AudioRecord 对象
        self._reader_thread: Optional[threading.Thread] = None
        self._queue: queue.Queue = queue.Queue(maxsize=64)
        self._lock = threading.Lock()

        # PCM 原始数据缓冲区（由读取线程填充，被切割线程消费）
        self._raw_buffer = bytearray()
        self._buffer_lock = threading.Lock()

        # AudioRecord 参数（在 start() 中初始化）
        self._buffer_size = 0

    # ----------------------------------------------------------
    # 公共接口
    # ----------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self):
        """
        启动音频采集。

        会依次:
          1. 检查 pyjnius 是否可用
          2. 初始化 AudioRecord
          3. 启动后台读取线程
          4. 开始录音
        """
        if self._running:
            return

        self._check_prerequisites()

        # 初始化 AudioRecord
        self._init_audio_record()

        # 启动读取线程
        self._running = True
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name="AndroidAudioReader",
            daemon=True,
        )
        self._reader_thread.start()

        # 开始录音
        self._start_recording()
        print("[AndroidAudio] 录音已启动")

    def stop(self):
        """停止音频采集并释放资源。"""
        with self._lock:
            self._running = False

        # 停止并释放 AudioRecord
        self._stop_recording()

        # 等待读取线程结束
        if self._reader_thread is not None and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=3)

        # 清空队列
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

        print("[AndroidAudio] 录音已停止")

    def get_chunk(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """
        获取下一个音频块（阻塞）。

        Args:
            timeout: 等待超时（秒）。None 表示一直阻塞。

        Returns:
            完整的 WAV 格式音频数据（bytes），超时返回 None。
        """
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def stream(self, timeout: Optional[float] = 10.0):
        """
        生成器，持续产出音频块直到停止。
        """
        while self._running:
            chunk = self.get_chunk(timeout=timeout)
            if chunk is None:
                continue
            yield chunk

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    # ----------------------------------------------------------
    # 内部方法：环境检查与初始化
    # ----------------------------------------------------------

    def _check_prerequisites(self):
        """启动前检查环境。"""
        if not is_android():
            print("[AndroidAudio] 警告：当前非 Android 环境，AudioRecord 不可用")
            # 不抛出异常，允许在非 Android 环境导入模块

        if not check_jnius():
            raise JniusNotAvailableError(
                "pyjnius 不可用。\n"
                "请在 buildozer.spec 的 requirements 中添加 pyjnius，\n"
                "并在 Android 设备上运行。"
            )

    def _init_audio_record(self):
        """
        初始化 Android AudioRecord。

        使用 pyjnius 调用 Java API:
          - 使用直接构造函数 (兼容 API 16+)
          - 音频参数通过 Java 常量配置
        """
        from jnius import autoclass

        # 获取 Java 类
        AudioRecord = autoclass('android.media.AudioRecord')
        AudioFormat = autoclass('android.media.AudioFormat')
        MediaRecorder = autoclass('android.media.MediaRecorder')

        # 音频参数
        audio_source = MediaRecorder.AudioSource.MIC       # 麦克风源
        channel_config = AudioFormat.CHANNEL_IN_MONO       # 单声道
        audio_format = AudioFormat.ENCODING_PCM_16BIT     # 16-bit PCM
        sample_rate = self.sample_rate

        # 计算最小缓冲区大小
        min_buffer_size = AudioRecord.getMinBufferSize(
            sample_rate,
            channel_config,
            audio_format,
        )

        if min_buffer_size <= 0:
            raise AudioRecordInitError(
                f"AudioRecord.getMinBufferSize 返回无效值: {min_buffer_size}\n"
                "可能是采样率不被支持（推荐 16000Hz）。"
            )

        # 使用 2 倍最小缓冲区以确保稳定性
        self._buffer_size = min_buffer_size * 2

        print(f"[AndroidAudio] 最小缓冲区: {min_buffer_size} bytes")
        print(f"[AndroidAudio] 实际使用缓冲区: {self._buffer_size} bytes")

        # 创建 AudioRecord 实例
        # 构造函数签名:
        #   AudioRecord(audioSource, sampleRateInHz, channelConfig, audioFormat, bufferSizeInBytes)
        try:
            self._recorder = AudioRecord(
                audio_source,
                sample_rate,
                channel_config,
                audio_format,
                self._buffer_size,
            )
        except Exception as e:
            raise AudioRecordInitError(f"创建 AudioRecord 失败: {e}")

        # 检查状态
        if self._recorder.getState() != AudioRecord.STATE_INITIALIZED:
            self._recorder.release()
            raise AudioRecordInitError(
                "AudioRecord 初始化失败，请检查麦克风权限是否已授予。"
            )

        # 预计算读取块大小（每次读取 0.1 秒的数据）
        self._read_chunk_size = sample_rate * BYTES_PER_SAMPLE // 10  # 3200 bytes for 16kHz
        if self._read_chunk_size > self._buffer_size:
            self._read_chunk_size = self._buffer_size // 2

        print(f"[AndroidAudio] AudioRecord 初始化成功，读取块大小: {self._read_chunk_size} bytes")

    def _start_recording(self):
        """开始录音。"""
        if self._recorder is None:
            raise AudioRecordInitError("AudioRecord 未初始化")
        self._recorder.startRecording()
        print("[AndroidAudio] AudioRecord.startRecording() 已调用")

    def _stop_recording(self):
        """停止录音并释放资源。"""
        if self._recorder is not None:
            try:
                self._recorder.stop()
            except Exception:
                pass
            try:
                self._recorder.release()
            except Exception:
                pass
            self._recorder = None
            print("[AndroidAudio] AudioRecord 已释放")

    # ----------------------------------------------------------
    # 内部方法：后台读取线程
    # ----------------------------------------------------------

    def _reader_loop(self):
        """
        后台读取线程。

        持续从 AudioRecord 读取 PCM 数据，写入 _raw_buffer，
        并切割成固定大小的块放入队列。
        """
        from jnius import autoclass
        AudioRecord = autoclass('android.media.AudioRecord')

        # 创建 Java byte[] 缓冲区
        # 使用 array('b') 创建有符号字节数组，然后通过 jnius 转换为 Java byte[]
        from array import array
        java_buffer = array('b', [0] * self._read_chunk_size)

        # 错误代码常量
        ERROR_INVALID_OPERATION = -1
        ERROR_BAD_VALUE = -2

        while self._running:
            try:
                # 从 AudioRecord 读取数据
                # read(byte[] audioData, int offsetInBytes, int sizeInBytes)
                # 在 pyjnius 中，可以直接传递 Python list 或 array，它会自动转换
                bytes_read = self._recorder.read(java_buffer, 0, len(java_buffer))

                if bytes_read > 0:
                    # 将读取的数据追加到缓冲区
                    # array('b') 可以直接转换为 bytes（自动处理符号）
                    chunk = bytes(java_buffer[:bytes_read])
                    with self._buffer_lock:
                        self._raw_buffer.extend(chunk)

                    # 切割并打包音频块
                    self._produce_chunks()
                elif bytes_read == ERROR_INVALID_OPERATION:
                    if self._running:
                        print("[AndroidAudio] 读取错误: ERROR_INVALID_OPERATION")
                    time.sleep(0.01)
                elif bytes_read == ERROR_BAD_VALUE:
                    if self._running:
                        print("[AndroidAudio] 读取错误: ERROR_BAD_VALUE")
                    time.sleep(0.01)
                else:
                    time.sleep(0.001)

            except Exception as e:
                if self._running:
                    print(f"[AndroidAudio] 读取线程异常: {e}")
                    import traceback
                    traceback.print_exc()
                time.sleep(0.01)

    def _produce_chunks(self):
        """
        从 _raw_buffer 中切割音频块，打包为 WAV 并放入队列。
        """
        while True:
            with self._buffer_lock:
                if len(self._raw_buffer) < self._chunk_bytes:
                    break

                # 取出一个完整的识别窗口
                chunk_pcm = bytes(self._raw_buffer[:self._chunk_bytes])
                # 滑动：只移除 hop 长度，保留重叠部分
                del self._raw_buffer[:self._hop_bytes]

            # 打包为完整 WAV 并放入队列
            chunk_wav = self._pcm_to_wav(chunk_pcm)
            try:
                self._queue.put(chunk_wav, timeout=1.0)
            except queue.Full:
                # 队列满了丢掉旧数据，保持实时性
                pass

    # ----------------------------------------------------------
    # 内部方法：WAV 打包
    # ----------------------------------------------------------

    def _pcm_to_wav(self, pcm_data: bytes) -> bytes:
        """
        将裸 PCM 数据包裹为完整的 WAV 格式。

        WAV 文件结构:
          [RIFF 头 (12 bytes)]
          [fmt 块 (24 bytes)]
          [data 块 (8 bytes + data)]
        """
        data_size = len(pcm_data)
        channels = 1
        bits_per_sample = 16
        byte_rate = self.sample_rate * channels * bits_per_sample // 8
        block_align = channels * bits_per_sample // 8
        fmt_chunk_size = 16
        audio_format = 1  # PCM = 1

        # RIFF 总大小 = 4 + (8 + 24) + (8 + data_size) = 36 + data_size
        # 不包含 RIFF 自己的 8 字节（"RIFF" + chunk_size）
        riff_chunk_size = 4 + 24 + (8 + data_size)  # = 36 + data_size

        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF",           # RIFF 标识
            riff_chunk_size,   # 整个文件大小 - 8
            b"WAVE",           # WAVE 标识
            b"fmt ",           # fmt 块
            fmt_chunk_size,    # fmt 块大小 (16)
            audio_format,      # 音频格式 (1 = PCM)
            channels,          # 声道数
            self.sample_rate,  # 采样率
            byte_rate,         # 字节率
            block_align,       # 块对齐
            bits_per_sample,   # 位深
            b"data",           # data 块
            data_size,         # 数据大小
        )

        return header + pcm_data

    # ----------------------------------------------------------
    # 属性
    # ----------------------------------------------------------

    @property
    def audio_format(self) -> dict:
        """返回当前音频格式信息。"""
        return {
            "sample_rate": self.sample_rate,
            "sample_width": BYTES_PER_SAMPLE,
            "channels": 1,
            "format": "PCM WAV (Android AudioRecord)",
            "window_seconds": self.chunk_duration,
            "hop_seconds": self.hop_duration,
            "overlap": f"{self.hop_duration / self.chunk_duration * 100:.0f}%",
        }

    @property
    def buffer_size(self) -> int:
        """返回 AudioRecord 缓冲区大小（bytes）。"""
        return self._buffer_size


# ============================================================
# 工厂函数：根据环境自动选择实现
# ============================================================

def get_audio_capture(use_android: Optional[bool] = None, **kwargs):
    """
    根据运行环境自动选择合适的音频采集实现。

    Args:
        use_android: 强制指定使用 Android 实现（True/False/None 表示自动检测）
        **kwargs: 传递给捕获类的参数（sample_rate, chunk_duration, hop_duration）

    Returns:
        AudioCapture 或 AndroidAudioCapture 实例
    """
    if use_android is None:
        use_android = is_android()

    if use_android:
        print("[AudioFactory] 使用 Android AudioRecord 采集")
        return AndroidAudioCapture(**kwargs)
    else:
        print("[AudioFactory] 使用 Termux 麦克风采集")
        from audio_capture import AudioCapture
        return AudioCapture(**kwargs)


# ============================================================
# 独立命令行测试
# ============================================================
if __name__ == "__main__":
    import sys

    print("=" * 50)
    print("粤语实时翻译 - Android 音频采集模块测试")
    print("=" * 50)

    if not is_android():
        print("\n[警告] 当前非 Android 环境！")
        print("  此模块需要在 Android 设备上运行。")
        print("  当前仅为接口验证，不会真正录音。\n")

    if not check_jnius():
        print("[检查] pyjnius... ✗ 不可用")
        print("  请在 Android 设备上安装 pyjnius")
        print("  buildozer.spec 添加: requirements = python3, pyjnius, ...")
        sys.exit(1)
    else:
        print("[检查] pyjnius... ✓ 可用")

    print(f"\n音频格式: {AndroidAudioCapture().audio_format}")

    print("\n开始录音测试（5 秒）...")
    print("  请对着手机麦克风说话...\n")

    capture = AndroidAudioCapture()
    try:
        capture.start()
    except Exception as e:
        print(f"启动失败: {e}")
        sys.exit(1)

    try:
        count = 0
        start_time = time.time()
        for chunk in capture.stream():
            elapsed = time.time() - start_time
            chunk_size_kb = len(chunk) / 1024
            print(f"  [{elapsed:5.1f}s] 收到音频块: {chunk_size_kb:.0f} KB")
            count += 1
            if elapsed >= 5.0:
                print(f"\n5 秒内收到 {count} 个音频块。")
                break
    except KeyboardInterrupt:
        print("\n用户中断。")
    finally:
        capture.stop()

    print("\n测试结束 ✓")
