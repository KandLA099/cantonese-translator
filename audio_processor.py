"""
audio_processor.py — 音频预处理 + VAD 检测模块
=================================================

连接 AudioCapture 和 ASREngine 的中间层。

核心功能:
  1. **VAD (语音活动检测)**: 使用 webrtcvad 检测音频块中是否包含人声
  2. **音频预处理**: WAV 解析、格式校验、电平归一化（递交给 ASR 前）
  3. **语音段追踪**: 标记连续语音段的开始和结束，辅助 UI 展示
  4. **静音过滤**: 静音块直接跳过推理，节省手机算力

工作流::

    processor = AudioProcessor()
    capture = AudioCapture()

    capture.start()
    for wav_chunk in capture.stream():
        result = processor.process(wav_chunk)
        if result["has_speech"]:
            # 有语音 → 发送给 ASR 引擎
            asr_engine.recognize(result["audio"])
        else:
            # 静音 → 跳过
            pass

VAD 参数说明:
  - webrtcvad 工作在 16kHz、16-bit 单声道 PCM 上
  - 帧大小: 30ms（每帧 480 样本）
  - 聚合模式: 连续 3 帧中有 ≥2 帧有语音 → 算有语音
  - 灵敏度: mode 0（最宽松）~ mode 3（最严格），默认 mode 1
"""

import struct
import time
import logging
from typing import Optional, Tuple, List, Dict, Any
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================
SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2           # 16-bit
FRAME_DURATION_MS = 30         # VAD 帧时长 (webrtcvad 支持 10/20/30ms)
FRAME_SIZE = SAMPLE_RATE * FRAME_DURATION_MS // 1000  # 480 样本
FRAME_BYTES = FRAME_SIZE * BYTES_PER_SAMPLE            # 960 字节
VAD_MODE = 1                   # VAD 灵敏度 (0=最松, 3=最严)


@dataclass
class ProcessedChunk:
    """
    处理后音频块的结构化信息。

    Attributes:
        has_speech:  是否检测到人声
        audio:       处理后的音频数据（WAV bytes），有语音时可用
        vad_frames:  语音帧占比 (0.0~1.0)
        segment_id:  当前语音段的 ID（用于 UI 区分不同发言）
        is_segment_start: 是否为新语音段的开始
        is_segment_end:   是否为当前语音段的结束
        duration_ms: 音频时长 (ms)
        level_db:    音频电平 (dBFS)
        timestamp:   处理时间戳
    """
    has_speech: bool = False
    audio: Optional[bytes] = None
    vad_frames: float = 0.0
    segment_id: int = 0
    is_segment_start: bool = False
    is_segment_end: bool = False
    duration_ms: float = 0.0
    level_db: float = -100.0
    timestamp: float = 0.0


class VADError(Exception):
    """VAD 处理错误。"""
    pass


class AudioProcessor:
    """
    音频处理器: VAD 检测 + 预处理 + 语音段追踪。

    Args:
        vad_mode: webrtcvad 灵敏度 (0-3, 默认 1)
        speech_frame_ratio: 判定为"有语音"的帧比例阈值 (默认 0.3)
        silence_timeout_ms: 连续静音超过此毫秒时标记段结束 (默认 1000ms)
        enable_vad: 是否启用 VAD（设为 False 时不过滤任何音频）
    """

    def __init__(
        self,
        vad_mode: int = VAD_MODE,
        speech_frame_ratio: float = 0.3,
        silence_timeout_ms: int = 1000,
        enable_vad: bool = True,
    ):
        self.vad_mode = vad_mode
        self.speech_frame_ratio = speech_frame_ratio
        self.silence_timeout_ms = silence_timeout_ms
        self.enable_vad = enable_vad

        # VAD 实例（延迟初始化）
        self._vad = None

        # 语音段追踪
        self._current_segment_id = 0
        self._last_speech_time = 0.0
        self._in_speech = False
        self._consecutive_silence_frames = 0
        self._silence_frames_threshold = silence_timeout_ms // FRAME_DURATION_MS

        # 统计
        self._processed_count = 0
        self._speech_count = 0

    # ----------------------------------------------------------
    # 核心处理
    # ----------------------------------------------------------

    def process(self, wav_bytes: bytes) -> ProcessedChunk:
        """
        处理一个音频块：解析 WAV → VAD 检测 → 预处理 → 返回结果。

        Args:
            wav_bytes: 完整的 WAV 文件字节数据

        Returns:
            ProcessedChunk 结构化结果
        """
        result = ProcessedChunk(timestamp=time.time())

        try:
            # 1. 解析 WAV → PCM 数据
            pcm_data, sample_rate = _extract_pcm(wav_bytes)
            result.duration_ms = len(pcm_data) / (sample_rate * BYTES_PER_SAMPLE) * 1000

            # 2. 计算音频电平
            result.level_db = _compute_level(pcm_data)

            if not self.enable_vad:
                # 跳过 VAD，全部放行
                result.has_speech = True
                result.audio = wav_bytes
                self._processed_count += 1
                self._speech_count += 1
                return result

            # 3. VAD 检测
            speech_ratio, is_speech = self._vad_detect(pcm_data)
            result.vad_frames = speech_ratio
            result.has_speech = is_speech

            # 4. 语音段追踪
            self._track_segment(is_speech, result)

            # 5. 如果检测到语音，保留音频
            if is_speech:
                result.audio = wav_bytes
                self._speech_count += 1

            self._processed_count += 1
            return result

        except VADError:
            # VAD 出错时，为保稳放行音频
            result.has_speech = True
            result.audio = wav_bytes
            return result

    # ----------------------------------------------------------
    # VAD 检测
    # ----------------------------------------------------------

    def _ensure_vad(self):
        """延迟初始化 VAD 实例。"""
        if self._vad is not None:
            return
        try:
            import webrtcvad
            self._vad = webrtcvad.Vad(self.vad_mode)
        except ImportError:
            logger.warning(
                "webrtcvad 未安装，回退到能量检测模式。\n"
                "如需更好效果: pip install webrtcvad"
            )
            self._vad = None

    def _vad_detect(self, pcm_data: bytes) -> Tuple[float, bool]:
        """
        对 PCM 数据执行 VAD 检测。

        Args:
            pcm_data: 16-bit PCM 字节数据

        Returns:
            (speech_frame_ratio, is_speech)
            - speech_frame_ratio: 有语音的帧占比 (0.0~1.0)
            - is_speech: 是否判定为有语音
        """
        self._ensure_vad()

        if len(pcm_data) < FRAME_BYTES:
            # 数据不足一帧，保守判为有语音
            return 0.0, True

        # 分割为 30ms 帧
        num_frames = len(pcm_data) // FRAME_BYTES
        speech_frames = 0

        if self._vad is not None:
            # 使用 webrtcvad
            for i in range(num_frames):
                frame = pcm_data[i * FRAME_BYTES:(i + 1) * FRAME_BYTES]
                try:
                    if self._vad.is_speech(frame, SAMPLE_RATE):
                        speech_frames += 1
                except Exception:
                    pass
        else:
            # 降级: 能量检测（RMS 阈值）
            rms_threshold = 500  # 经验值，对应 16-bit PCM
            for i in range(num_frames):
                frame = pcm_data[i * FRAME_BYTES:(i + 1) * FRAME_BYTES]
                samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
                rms = np.sqrt(np.mean(samples ** 2))
                if rms > rms_threshold:
                    speech_frames += 1

        ratio = speech_frames / max(num_frames, 1)
        is_speech = ratio >= self.speech_frame_ratio
        return ratio, is_speech

    # ----------------------------------------------------------
    # 语音段追踪
    # ----------------------------------------------------------

    def _track_segment(self, is_speech: bool, result: ProcessedChunk):
        """
        追踪连续语音段，标记段的开始和结束。

        如果连续 N 帧静音 → 当前段结束
        如果在段外检测到语音 → 新段开始
        """
        now = time.time()

        if is_speech:
            self._consecutive_silence_frames = 0
            self._last_speech_time = now

            if not self._in_speech:
                # 静音 → 语音 的切换，新段开始
                self._in_speech = True
                self._current_segment_id += 1
                result.is_segment_start = True
                result.segment_id = self._current_segment_id
            else:
                # 继续当前段
                result.segment_id = self._current_segment_id

        else:
            self._consecutive_silence_frames += 1

            if self._in_speech:
                result.segment_id = self._current_segment_id

                # 检查是否超时结束
                if self._consecutive_silence_frames >= self._silence_frames_threshold:
                    self._in_speech = False
                    result.is_segment_end = True
            else:
                result.segment_id = self._current_segment_id

    # ----------------------------------------------------------
    # 重设 / 统计
    # ----------------------------------------------------------

    def reset(self):
        """重设语音段追踪状态（开始新会话时调用）。"""
        self._current_segment_id = 0
        self._last_speech_time = 0.0
        self._in_speech = False
        self._consecutive_silence_frames = 0
        self._processed_count = 0
        self._speech_count = 0

    @property
    def stats(self) -> dict:
        """处理统计。"""
        return {
            "processed_chunks": self._processed_count,
            "speech_chunks": self._speech_count,
            "speech_ratio": round(
                self._speech_count / max(self._processed_count, 1), 3
            ),
            "vad_mode": self.vad_mode,
            "vad_enabled": self.enable_vad,
        }

    def __str__(self) -> str:
        return (
            f"AudioProcessor(vad_mode={self.vad_mode}, "
            f"speech_ratio={self.speech_frame_ratio}, "
            f"enabled={self.enable_vad})"
        )


# ============================================================
# 独立工具函数
# ============================================================

def _extract_pcm(wav_bytes: bytes) -> Tuple[bytes, int]:
    """
    从 WAV 字节中提取 PCM 数据。

    Args:
        wav_bytes: 完整 WAV 数据

    Returns:
        (pcm_data: bytes, sample_rate: int)

    Raises:
        VADError: WAV 格式无效
    """
    if len(wav_bytes) < 44:
        raise VADError(f"WAV 数据过短 ({len(wav_bytes)} bytes)")

    try:
        sample_rate = int.from_bytes(wav_bytes[24:28], "little")

        # 定位 data 块
        pos = 12
        while pos < len(wav_bytes) - 8:
            chunk_id = wav_bytes[pos:pos+4]
            chunk_size = int.from_bytes(wav_bytes[pos+4:pos+8], "little")
            if chunk_id == b"data":
                data_start = pos + 8
                data_size = min(chunk_size, len(wav_bytes) - data_start)
                pcm_data = wav_bytes[data_start:data_start + data_size]
                return pcm_data, sample_rate
            pos += 8 + chunk_size

        # 没找到 data 块，尝试默认偏移
        pcm_data = wav_bytes[44:]
        return pcm_data, sample_rate

    except (IndexError, struct.error) as e:
        raise VADError(f"WAV 解析失败: {e}")


def _compute_level(pcm_data: bytes) -> float:
    """
    计算音频电平 (dBFS)。

    Args:
        pcm_data: 16-bit PCM 数据

    Returns:
        电平值 (dBFS)，全静音时返回 -100
    """
    if len(pcm_data) < 2:
        return -100.0

    try:
        samples = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32)
        rms = np.sqrt(np.mean(samples ** 2))
        if rms < 1.0:
            return -100.0
        return 20.0 * np.log10(rms / 32768.0)
    except Exception:
        return -100.0


def is_silent(pcm_data: bytes, threshold_db: float = -40.0) -> bool:
    """
    快速检测音频块是否静音（不实例化 VAD）。

    Args:
        pcm_data: 16-bit PCM 数据
        threshold_db: 静音阈值 (dBFS)，默认 -40dB

    Returns:
        True 如果音频电平低于阈值
    """
    level = _compute_level(pcm_data)
    return level < threshold_db


# ============================================================
# 命令行测试
# ============================================================
if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    print("=" * 50)
    print("粤语实时翻译 - 音频处理模块测试")
    print("=" * 50)

    # 生成测试 WAV（1秒静音）
    import struct
    duration = 1.0
    num_samples = int(SAMPLE_RATE * duration)

    print(f"\n生成测试音频: {duration}秒 静音, {SAMPLE_RATE}Hz 16-bit 单声道")

    # 产生一些带"语音"的测试数据（方波模拟人声）
    test_samples = []
    for i in range(num_samples):
        # 前 0.5 秒有点噪声，后 0.5 秒完全静音
        if i < num_samples // 2:
            val = int(np.sin(2 * np.pi * 200 * i / SAMPLE_RATE) * 8000)
        else:
            val = 0
        test_samples.append(max(-32768, min(32767, val)))

    pcm_data = struct.pack(f"<{len(test_samples)}h", *test_samples)

    # 构造 WAV
    wav_bytes = (
        b"RIFF" + struct.pack("<I", 36 + len(pcm_data)) +
        b"WAVEfmt " + struct.pack("<IHHIIHH",
            16, 1, 1, SAMPLE_RATE,
            SAMPLE_RATE * BYTES_PER_SAMPLE,
            BYTES_PER_SAMPLE, 16
        ) +
        b"data" + struct.pack("<I", len(pcm_data)) +
        pcm_data
    )

    # 测试处理器
    processor = AudioProcessor(enable_vad=True)
    result = processor.process(wav_bytes)

    print(f"\n处理结果:")
    print(f"  has_speech:  {result.has_speech}")
    print(f"  vad_frames:  {result.vad_frames:.2f}")
    print(f"  segment_id:  {result.segment_id}")
    print(f"  level_db:    {result.level_db:.1f} dBFS")
    print(f"  duration_ms: {result.duration_ms:.0f} ms")
    print(f"  stats:       {processor.stats}")

    print(f"\nVAD {'检测到' if result.has_speech else '未检测到'}语音")
    print(f"测试完成 ✓")
