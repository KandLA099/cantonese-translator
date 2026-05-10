"""
audio_capture.py — 音频逐秒采集模块
====================================

基于 termux-microphone-record 命令实现 Android 麦克风实时音频采集。

工作方式:
  1. 启动 termux-microphone-record 在后台连续录音到临时 WAV 文件
  2. 后台线程以 100ms 为间隔增量读取音频数据
  3. 跳过 WAV 文件头（44 字节），直接读取原始 PCM 数据
  4. 用滑动窗口切割成 1 秒/0.5 秒步进的音频块
  5. 每块重新打包成标准 WAV，放入线程安全队列供下游消费

音频格式: 16kHz, 16-bit, 单声道, PCM WAV
"""

import struct
import subprocess
import tempfile
import threading
import time
import os
import queue
import signal
from pathlib import Path
from typing import Optional, BinaryIO


# ============================================================
# 常量
# ============================================================
WAV_HEADER_SIZE = 44  # 标准 PCM WAV 文件头大小
DEFAULT_SAMPLE_RATE = 16000  # 采样率 16kHz
DEFAULT_CHUNK_DURATION = 1.0  # 每个识别窗口 1 秒
DEFAULT_HOP_DURATION = 0.5  # 滑动步进 0.5 秒（50% 重叠）
BYTES_PER_SAMPLE = 2  # 16-bit = 2 bytes


class AudioCaptureError(Exception):
    """音频采集相关错误的基类。"""
    pass


class MicrophonePermissionError(AudioCaptureError):
    """未授予麦克风权限。"""
    pass


class TermuxApiNotFoundError(AudioCaptureError):
    """未安装 Termux:API 插件。"""
    pass


class AudioCapture:
    """
    实时音频采集器。

    用法::

        capture = AudioCapture()
        capture.start()

        # 在主循环中消费音频块
        while capture.is_running:
            chunk_wav = capture.get_chunk(timeout=1.0)
            if chunk_wav:
                # chunk_wav 是 bytes，包含完整的 WAV 数据
                process_chunk(chunk_wav)

        capture.stop()

    也支持作为上下文管理器使用::

        with AudioCapture() as capture:
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
        self._process: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._queue: queue.Queue = queue.Queue(maxsize=64)
        self._temp_dir: Optional[Path] = None
        self._recording_file: Optional[Path] = None
        self._lock = threading.Lock()

    # ----------------------------------------------------------
    # 公共接口
    # ----------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self):
        """
        启动音频采集。

        会依次检查:
          1. termux-microphone-record 命令是否存在
          2. 麦克风是否有写入权限（首次运行会弹出系统权限请求）
        """
        if self._running:
            return

        self._check_prerequisites()

        self._temp_dir = Path(tempfile.mkdtemp(prefix="cantonese_audio_"))
        self._recording_file = self._temp_dir / "live.wav"

        # 启动后台录音进程
        self._process = subprocess.Popen(
            [
                "termux-microphone-record",
                "-f", str(self._recording_file),
                "-r", str(self.sample_rate),
                "-c", "1",
                "-e", "wav",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        self._running = True

        # 启动读取线程
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name="AudioReader",
            daemon=True,
        )
        self._reader_thread.start()

    def stop(self):
        """停止音频采集并清理临时文件。"""
        with self._lock:
            self._running = False

        # 发送退出信号给 termux-microphone-record
        try:
            subprocess.run(
                ["termux-microphone-record", "-q"],
                capture_output=True,
                timeout=2,
            )
        except Exception:
            pass

        # 终止后台进程
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=2)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass

        # 等待读取线程结束
        if self._reader_thread is not None and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=3)

        # 清理临时文件
        if self._temp_dir is not None:
            import shutil
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir = None

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
        适合在消费循环中使用: ``for chunk in capture.stream():``
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
    # 内部方法
    # ----------------------------------------------------------

    @staticmethod
    def check_available() -> bool:
        """检查 termux-microphone-record 是否可用。"""
        try:
            result = subprocess.run(
                ["termux-microphone-record", "--help"],
                capture_output=True,
                timeout=3,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    @staticmethod
    def check_permission() -> bool:
        """
        检查麦克风权限是否已授予。
        通过尝试录制 0.1 秒来验证。
        """
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                test_file = f.name
            result = subprocess.run(
                [
                    "termux-microphone-record",
                    "-d", "0.1",
                    "-f", test_file,
                    "-r", "16000",
                    "-c", "1",
                    "-e", "wav",
                ],
                capture_output=True,
                timeout=5,
            )
            # 清理
            try:
                os.unlink(test_file)
            except Exception:
                pass
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _check_prerequisites(self):
        """启动前检查环境和权限。"""
        if not self.check_available():
            raise TermuxApiNotFoundError(
                "未找到 termux-microphone-record 命令。\n"
                "请确保已从 F-Droid 安装 Termux:API，\n"
                "并在 Termux 中运行: pkg install termux-api"
            )
        # 不在这里阻塞权限检查，首次运行会自然触发系统权限弹窗

    def _reader_loop(self):
        """
        后台读取线程。

        从持续增长的 WAV 文件中增量读取原始 PCM 数据，
        用滑动窗口切割并重新打包为完整 WAV 块。
        """
        # 等待文件创建并写入头信息
        if not self._wait_for_file():
            return

        # 跳过 WAV 文件头，从数据区开始读取
        data_offset = WAV_HEADER_SIZE
        prev_read_pos = data_offset

        raw_buffer = bytearray()

        while self._running:
            try:
                current_size = self._recording_file.stat().st_size

                if current_size > prev_read_pos:
                    with open(self._recording_file, "rb") as f:
                        f.seek(prev_read_pos)
                        new_data = f.read(min(
                            current_size - prev_read_pos,
                            65536,  # 每次最多读 64KB，防止一次读太大
                        ))
                        raw_buffer.extend(new_data)
                    prev_read_pos += len(new_data)

                # 从缓冲区中切割完整的识别窗口
                while len(raw_buffer) >= self._chunk_bytes:
                    chunk_pcm = bytes(raw_buffer[:self._chunk_bytes])
                    # 滑动：只移除 hop 长度，保留重叠部分
                    raw_buffer = raw_buffer[self._hop_bytes:]
                    # 打包为完整 WAV 并放入队列
                    chunk_wav = self._pcm_to_wav(chunk_pcm)
                    try:
                        self._queue.put(chunk_wav, timeout=1.0)
                    except queue.Full:
                        # 队列满了丢掉旧数据，保持实时性
                        pass

            except FileNotFoundError:
                # 文件可能还没完全创建好，重试
                pass
            except PermissionError:
                if self._running:
                    print("[AudioCapture] 权限错误，可能是麦克风被占用？")
            except Exception:
                pass

            time.sleep(0.1)  # 每 100ms 轮询一次

    def _wait_for_file(self, max_wait: float = 3.0) -> bool:
        """等待录音文件创建完成。"""
        elapsed = 0.0
        while self._running and elapsed < max_wait:
            if (self._recording_file is not None
                    and self._recording_file.exists()
                    and self._recording_file.stat().st_size >= WAV_HEADER_SIZE):
                return True
            time.sleep(0.1)
            elapsed += 0.1
        return False

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

        # RIFF 总大小 = 36 + data_size（不包含 RIFF 自己的 8 字节标识和尺寸）
        riff_chunk_size = 36 + data_size

        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF",
            riff_chunk_size,
            b"WAVE",
            b"fmt ",
            fmt_chunk_size,      # fmt 块大小
            1,                   # 音频格式: 1 = PCM
            channels,            # 声道数: 1
            self.sample_rate,    # 采样率: 16000
            byte_rate,           # 字节率: 32000
            block_align,         # 块对齐: 2
            bits_per_sample,     # 位深: 16
            b"data",
            data_size,           # 数据大小
        )

        return header + pcm_data

    @property
    def audio_format(self) -> dict:
        """返回当前音频格式信息。"""
        return {
            "sample_rate": self.sample_rate,
            "sample_width": BYTES_PER_SAMPLE,
            "channels": 1,
            "format": "PCM WAV",
            "window_seconds": self.chunk_duration,
            "hop_seconds": self.hop_duration,
            "overlap": f"{self.hop_duration / self.chunk_duration * 100:.0f}%",
        }


# ============================================================
# 独立命令行测试
# ============================================================
if __name__ == "__main__":
    import sys

    print("=" * 50)
    print("粤语实时翻译 - 音频采集模块测试")
    print("=" * 50)

    # 检查环境
    print(f"\n[检查] termux-microphone-record... ", end="")
    if AudioCapture.check_available():
        print("✓ 可用")
    else:
        print("✗ 未找到！请安装 Termux:API")
        print("  步骤: pkg install termux-api")
        sys.exit(1)

    print(f"[检查] 麦克风权限... ", end="")
    if AudioCapture.check_permission():
        print("✓ 已授权")
    else:
        print("⚠ 待确认（首次运行时会弹出授权）")

    print(f"\n音频格式: {AudioCapture().audio_format}")

    print(f"\n开始录音 5 秒测试...")
    print("  请对着手机麦克风说话...\n")

    capture = AudioCapture()
    capture.start()

    try:
        count = 0
        start_time = time.time()
        for chunk in capture.stream():
            elapsed = time.time() - start_time
            chunk_size_kb = len(chunk) / 1024
            print(f"  [{elapsed:5.1f}s] 收到音频块: {chunk_size_kb:.0f} KB", flush=True)
            count += 1
            if elapsed >= 5.0:
                print(f"\n5 秒内收到 {count} 个音频块。")
                break
    except KeyboardInterrupt:
        print("\n用户中断。")
    finally:
        capture.stop()

    print("\n测试结束 ✓")
