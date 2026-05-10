"""
asr_engine.py — ASR 推理引擎
=============================

基于 ONNX Runtime + SenseVoice Small 模型实现离线语音识别。

工作流程:
  1. 加载 SenseVoice Small ONNX 模型 (~200MB) 和对应的 tokenizer
  2. 接收 WAV 音频数据，预处理为模型输入格式（float32 归一化波形）
  3. 执行 ONNX 推理
  4. 解码模型输出 token IDs 为简体中文文字
  5. 返回识别结果字符串

SenseVoice 特色:
  - 原生支持粤语 (yue) 等多语种语音识别
  - 输出直接映射为普通话简体中文，无需额外翻译层
  - 对粤语9个声调、入声韵尾等均经专门训练

使用示例::

    engine = SenseVoiceEngine(model_dir="models/")
    engine.load()
    text = engine.recognize(wav_bytes)
    print(f"识别结果: {text}")
"""

import os
import re
import time
import logging
from pathlib import Path
from typing import Optional, List, Tuple, Dict

import numpy as np

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================
SAMPLE_RATE = 16000          # 模型期望的输入采样率
BYTES_PER_SAMPLE = 2         # 16-bit PCM


class ModelLoadError(Exception):
    """模型加载失败。"""
    pass


class InferenceError(Exception):
    """推理过程出错。"""
    pass


class Tokenizer:
    """
    SenseVoice 的简易 tokenizer / 解码器。

    将模型输出的 token IDs 序列映射为中文文字。
    tokens.txt 文件格式: 每行 "<token_id> <token>" 或 "<token>"
    """

    def __init__(self, vocab_path: str):
        self.vocab_path = Path(vocab_path)
        self.id_to_token: Dict[int, str] = {}
        self.token_to_id: Dict[str, int] = {}
        self._loaded = False

    def load(self):
        """加载词汇表文件。"""
        if self._loaded:
            return
        if not self.vocab_path.exists():
            raise ModelLoadError(
                f"词汇表文件不存在: {self.vocab_path}\n"
                f"请先运行 models/download_model.sh 下载模型文件"
            )

        with open(self.vocab_path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                # 支持两种格式: "id token" 或 "token"
                parts = line.split(maxsplit=1)
                if len(parts) == 2 and parts[0].isdigit():
                    token_id = int(parts[0])
                    token = parts[1]
                else:
                    token = line
                    token_id = line_num

                self.id_to_token[token_id] = token
                self.token_to_id[token] = token_id

        logger.info(
            f"词汇表已加载: {len(self.id_to_token)} 个 token"
        )
        self._loaded = True

    def decode(self, token_ids: List[int]) -> str:
        """
        将 token ID 序列解码为文字。

        处理逻辑:
          1. 过滤特殊 token（<sos>, <eos>, <pad>, <blank> 等）
          2. 去重连续重复（CTC 风格重复消除）
          3. 拼接为字符串
        """
        if not self._loaded:
            self.load()

        # 特殊 token 前缀，这些应该被过滤
        special_prefixes = ("<", "[", "{")

        chars = []
        prev_token = None

        for tid in token_ids:
            token = self.id_to_token.get(tid, "")
            if not token:
                continue
            # 过滤特殊 token
            if token.startswith(special_prefixes):
                continue
            # CTC 重复消除
            if token == prev_token:
                continue
            # 空格处理：连续字符间的空格保留，首尾去除
            chars.append(token)
            prev_token = token

        text = "".join(chars).strip()
        return text


class SenseVoiceEngine:
    """
    SenseVoice ASR 推理引擎。

    管理 ONNX 模型的加载、音频预处理、推理执行和结果解码。

    Args:
        model_dir: 模型文件所在目录（应包含 model.onnx, tokens.txt 等）
        num_threads: ONNX Runtime 线程数（手机端建议 2-4）
        use_gpu: 是否启用 GPU/NPU 加速（需要 onnxruntime-silicon）
    """

    def __init__(
        self,
        model_dir: str = "models",
        num_threads: int = 2,
        use_gpu: bool = False,
    ):
        self.model_dir = Path(model_dir)
        self.model_path = self.model_dir / "model.onnx"
        self.tokens_path = self.model_dir / "tokens.txt"
        self.num_threads = num_threads
        self.use_gpu = use_gpu

        self._session = None
        self._tokenizer: Optional[Tokenizer] = None
        self._input_names: List[str] = []
        self._output_names: List[str] = []
        self._loaded = False

        # 性能统计
        self._inference_count = 0
        self._total_inference_time = 0.0

    # ----------------------------------------------------------
    # 加载 / 卸载
    # ----------------------------------------------------------

    def load(self):
        """
        加载 ONNX 模型和 tokenizer。

        Raises:
            ModelLoadError: 模型文件缺失或加载失败
        """
        self._validate_files()

        # 加载 Tokenizer
        logger.info("加载词汇表...")
        self._tokenizer = Tokenizer(str(self.tokens_path))
        try:
            self._tokenizer.load()
        except Exception as e:
            raise ModelLoadError(f"词汇表加载失败: {e}")

        # 加载 ONNX 模型
        logger.info(f"加载模型: {self.model_path}")
        try:
            import onnxruntime as ort
        except ImportError:
            raise ModelLoadError(
                "onnxruntime 未安装。请在 Termux 中运行:\n"
                "  pip install onnxruntime"
            )

        try:
            sess_options = ort.SessionOptions()
            sess_options.intra_op_num_threads = self.num_threads
            sess_options.inter_op_num_threads = 1
            sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            sess_options.enable_cpu_mem_arena = True
            sess_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL

            # 尝试加载
            if self.use_gpu:
                # 尝试 NPU / GPU 提供程序
                providers = [
                    ("QNNExecutionProvider", {}),
                    "CPUExecutionProvider",
                ]
            else:
                providers = ["CPUExecutionProvider"]

            self._session = ort.InferenceSession(
                str(self.model_path),
                sess_options=sess_options,
                providers=providers,
            )

            # 解析模型输入输出
            self._input_names = [inp.name for inp in self._session.get_inputs()]
            self._output_names = [out.name for out in self._session.get_outputs()]

            logger.info(
                f"模型已加载: 输入={self._input_names}, 输出={self._output_names}"
            )
            self._loaded = True

        except Exception as e:
            raise ModelLoadError(f"模型加载失败: {e}")

    def unload(self):
        """释放模型资源。"""
        self._session = None
        self._tokenizer = None
        self._loaded = False
        self._inference_count = 0
        self._total_inference_time = 0.0
        logger.info("模型资源已释放")

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def _validate_files(self):
        """检查模型文件完整性。"""
        missing = []
        if not self.model_path.exists():
            missing.append(str(self.model_path))
        if not self.tokens_path.exists():
            missing.append(str(self.tokens_path))

        if missing:
            raise ModelLoadError(
                "以下模型文件缺失:\n  " + "\n  ".join(missing) + "\n"
                "请运行 models/download_model.sh 下载模型"
            )

    # ----------------------------------------------------------
    # 音频预处理
    # ----------------------------------------------------------

    @staticmethod
    def parse_wav(wav_bytes: bytes) -> Tuple[np.ndarray, int]:
        """
        解析 WAV 字节数据，提取 PCM 样本数组。

        Args:
            wav_bytes: 完整的 WAV 文件数据（含 44 字节 RIFF 头）

        Returns:
            (samples_norm: float32 numpy array [-1, 1],
             sample_rate: int)
        """
        if len(wav_bytes) < WAV_HEADER_MIN_SIZE:
            raise ValueError(f"WAV 数据过短: {len(wav_bytes)} bytes")

        # 解析 WAV 头关键字段
        # 偏移 22: 声道数 (2 bytes)
        # 偏移 24: 采样率 (4 bytes)
        # 偏移 34: 位深 (2 bytes)
        channels = int.from_bytes(wav_bytes[22:24], "little")
        sample_rate = int.from_bytes(wav_bytes[24:28], "little")
        bits_per_sample = int.from_bytes(wav_bytes[34:36], "little")

        # 找到 data 块起始位置
        data_start = 44  # 标准 PCM WAV 头大小
        # 有的 WAV 文件包含额外块，需要搜索 "data" 标记
        pos = 12  # 跳过 RIFF 头
        while pos < len(wav_bytes) - 8:
            chunk_id = wav_bytes[pos:pos+4]
            chunk_size = int.from_bytes(wav_bytes[pos+4:pos+8], "little")
            if chunk_id == b"data":
                data_start = pos + 8
                data_size = chunk_size
                break
            pos += 8 + chunk_size
        else:
            # 没找到 data 块，用默认偏移
            data_start = 44
            data_size = len(wav_bytes) - data_start

        # 提取 PCM 数据
        raw_data = wav_bytes[data_start:data_start + data_size]

        if bits_per_sample == 16:
            dtype = "<i2"  # signed 16-bit little-endian
            samples_int = np.frombuffer(raw_data, dtype=np.int16).astype(np.float32)
            samples_norm = samples_int / 32768.0  # [-1, 1]
        elif bits_per_sample == 8:
            samples_int = np.frombuffer(raw_data, dtype=np.uint8).astype(np.float32)
            samples_norm = (samples_int - 128) / 128.0
        elif bits_per_sample == 32:
            samples_int = np.frombuffer(raw_data, dtype=np.int32).astype(np.float32)
            samples_norm = samples_int / 2147483648.0
        else:
            raise ValueError(f"不支持的位深: {bits_per_sample}")

        # 多声道取平均
        if channels > 1:
            samples_norm = samples_norm.reshape(-1, channels).mean(axis=1)

        # 如果采样率不是 16kHz，重采样（使用 linear interpolation）
        if sample_rate != SAMPLE_RATE:
            samples_norm = resample_audio(samples_norm, sample_rate, SAMPLE_RATE)

        return samples_norm, SAMPLE_RATE

    @staticmethod
    def normalize_audio(samples_norm: np.ndarray) -> np.ndarray:
        """
        音频归一化到 [-1, 1] 范围，避免过于安静或削波。

        使用 RMS 归一化，目标 RMS = 0.2
        """
        rms = np.sqrt(np.mean(samples_norm ** 2))
        if rms < 1e-8:
            return samples_norm  # 全静音，不做处理
        target_rms = 0.2
        gain = target_rms / rms
        gain = np.clip(gain, 0.1, 3.0)  # 限制增益范围
        return np.clip(samples_norm * gain, -1.0, 1.0)

    # ----------------------------------------------------------
    # 模型推理
    # ----------------------------------------------------------

    def _prepare_inputs(self, samples: np.ndarray) -> Dict[str, np.ndarray]:
        """
        将预处理后的音频样本转换为模型输入格式。

        SenseVoice ONNX 模型的典型输入:
          - "speech": float32 [1, audio_len]
          - "speech_lengths": int64 [1] (实际音频长度)

        实际接口以加载时解析的 input_names 为准。
        """
        # 确保是 2D: [1, seq_len]
        if samples.ndim == 1:
            samples = samples[np.newaxis, :]

        # 构建输入字典，根据模型实际输入名适配
        inputs = {}
        for name in self._input_names:
            name_lower = name.lower()
            if "speech" in name_lower and "length" not in name_lower:
                inputs[name] = samples.astype(np.float32)
            elif "length" in name_lower or "len" in name_lower:
                inputs[name] = np.array([samples.shape[1]], dtype=np.int64)
            elif "ratio" in name_lower:
                inputs[name] = np.array([1.0], dtype=np.float32)
            else:
                # 未知输入，用默认值填充
                logger.warning(f"未知模型输入: {name}, 使用默认值")
                shape_info = self._session.get_inputs()
                for inp in shape_info:
                    if inp.name == name:
                        dtype = np.float32 if "float" in inp.type.lower() else np.int64
                        shape = [1, 1]  # 默认 shape
                        inputs[name] = np.zeros(shape, dtype=dtype)
                        break

        return inputs

    def infer(self, samples: np.ndarray) -> List[int]:
        """
        执行 ONNX 推理，返回 token ID 序列。

        Args:
            samples: 归一化的 float32 音频样本 (shape: [n_samples] 或 [1, n_samples])

        Returns:
            token IDs 列表

        Raises:
            InferenceError: 推理过程中出错
        """
        if not self._loaded:
            raise InferenceError("模型未加载，请先调用 load()")

        try:
            inputs = self._prepare_inputs(samples)

            t0 = time.perf_counter()
            outputs = self._session.run(self._output_names, inputs)
            elapsed = time.perf_counter() - t0

            self._inference_count += 1
            self._total_inference_time += elapsed

            logger.debug(f"推理耗时: {elapsed*1000:.0f}ms")

            # 解析输出：尝试找到 token IDs
            token_ids = self._parse_output(outputs)
            return token_ids

        except Exception as e:
            raise InferenceError(f"推理失败: {e}")

    def _parse_output(self, outputs: List[np.ndarray]) -> List[int]:
        """
        解析模型输出，提取 token ID 序列。

        处理策略:
          1. 遍历所有输出 Tensor
          2. 如果是整数序列（token IDs），直接使用
          3. 如果是概率分布（logits），取 argmax
          4. 去重和过滤空白 token
        """
        candidates = []

        for idx, out in enumerate(outputs):
            name = self._output_names[idx]
            name_lower = name.lower()

            # 输出形状检查
            if out.ndim == 1:
                # 已经是 1D token 序列
                tokens = out.tolist()
                if all(isinstance(t, (int, np.integer)) for t in tokens):
                    candidates.append((name, [int(t) for t in tokens]))
            elif out.ndim == 2:
                # [1, seq_len] token 序列
                tokens = out[0].tolist()
                candidates.append((name, [int(t) for t in tokens]))
            elif out.ndim == 3:
                # [1, seq_len, vocab_size] logits 概率分布
                logits = out[0]  # [seq_len, vocab_size]
                tokens = np.argmax(logits, axis=-1).tolist()
                candidates.append((name, [int(t) for t in tokens]))

        if not candidates:
            raise InferenceError(
                f"无法解析模型输出。输出形状: "
                f"{[(n, str(o.shape)) for n, o in zip(self._output_names, outputs)]}"
            )

        # 选择第一个有效的输出
        chosen = candidates[0][1]

        # 后处理：过滤空白/填充 token (ID=0 通常为 blank/pad)
        chosen = [t for t in chosen if t != 0]

        # 去重连续重复 (CTC collapse)
        deduped = []
        for t in chosen:
            if not deduped or t != deduped[-1]:
                deduped.append(t)

        return deduped

    # ----------------------------------------------------------
    # 完整识别流程
    # ----------------------------------------------------------

    def recognize(self, wav_bytes: bytes) -> str:
        """
        完整识别流程: WAV → 预处理 → 推理 → 解码。

        Args:
            wav_bytes: 完整 WAV 文件字节数据

        Returns:
            识别出的简体中文文字
        """
        if not self._loaded:
            raise ModelLoadError("模型未加载，请先调用 load()")

        # 1. 解析 WAV
        samples, sr = self.parse_wav(wav_bytes)

        # 2. 归一化
        samples = self.normalize_audio(samples)

        # 3. 推理
        token_ids = self.infer(samples)

        # 4. 解码
        text = self._tokenizer.decode(token_ids)

        return text

    # ----------------------------------------------------------
    # 性能统计
    # ----------------------------------------------------------

    @property
    def stats(self) -> dict:
        """推理性能统计。"""
        avg_time = (
            self._total_inference_time / self._inference_count
            if self._inference_count > 0
            else 0
        )
        return {
            "inference_count": self._inference_count,
            "total_time_sec": round(self._total_inference_time, 3),
            "avg_time_ms": round(avg_time * 1000, 1),
            "model_path": str(self.model_path),
            "num_threads": self.num_threads,
        }

    def __enter__(self):
        if not self._loaded:
            self.load()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.unload()


# ============================================================
# 工具函数
# ============================================================

WAV_HEADER_MIN_SIZE = 44  # 最小有效 WAV 头长度


def resample_audio(
    samples: np.ndarray,
    orig_sr: int,
    target_sr: int,
) -> np.ndarray:
    """
    音频重采样（线性插值）。

    使用 scipy.signal.resample 或手动线性插值。
    当 scipy 不可用时的降级方案。
    """
    if orig_sr == target_sr:
        return samples

    try:
        from scipy import signal
        # 计算目标长度
        num_samples = int(len(samples) * target_sr / orig_sr)
        return signal.resample(samples, num_samples)
    except ImportError:
        # 手动线性插值（降级方案）
        num_samples = int(len(samples) * target_sr / orig_sr)
        x_old = np.linspace(0, 1, len(samples))
        x_new = np.linspace(0, 1, num_samples)
        return np.interp(x_new, x_old, samples)


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
    print("粤语实时翻译 - ASR 引擎测试")
    print("=" * 50)

    # 检查模型文件
    model_dir = Path(__file__).parent / "models"
    if not (model_dir / "model.onnx").exists():
        print(f"\n[警告] 未找到模型文件: {model_dir / 'model.onnx'}")
        print("请先运行: bash models/download_model.sh")
        print("然后重试本测试。\n")
        sys.exit(1)

    engine = SenseVoiceEngine(model_dir=str(model_dir))

    try:
        engine.load()
        print(f"\n模型加载成功!")
        print(f"模型信息: {engine.stats}")
        print(f"\n等待音频输入...")
        print("请先通过 audio_capture.py 采集音频文件传入。")
        print()

        # 测试空音频
        import struct
        empty_wav = audio_capture_raw()
        if empty_wav:
            text = engine.recognize(empty_wav)
            print(f"识别结果: '{text}'")

    except ModelLoadError as e:
        print(f"\n[错误] {e}")
    except KeyboardInterrupt:
        print("\n用户中断。")
    finally:
        engine.unload()
