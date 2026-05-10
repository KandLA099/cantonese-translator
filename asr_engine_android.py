"""
asr_engine_android.py — Android ASR 推理引擎
============================================

使用 Microsoft ONNX Runtime Android AAR + pyjnius 实现 ASR 推理。
这是 `android_audio.py` 套餐的一部分，与 SenseVoice 模型配合使用。

## 架构说明

Python onnxruntime pip 包没有 Android wheel。
替代方案：从 Maven Central 下载官方 Android AAR，
提取 libonnxruntime.so，通过 pyjnius JNI 调用 Java API。

## Maven 依赖

<dependency>
    <groupId>com.microsoft.onnxruntime</groupId>
    <artifactId>onnxruntime-android</artifactId>
    <version>1.17.1</version>
</dependency>

Java 包名: ai.onnxruntime
官方文档: https://onnxruntime.ai/docs/install/#android

## 已知限制

1. pyjnius 调用 Java ONNX Runtime API 比较复杂（语法和 Python API 不同）
2. 需要正确处理 Java array/map 对象
3. 模型输出解码依赖于具体的 SenseVoice 模型格式
4. 建议在真机/模拟器上测试

## 备选方案

如果 pyjnius + ONNX Runtime Android AAR 方案不稳定，可以考虑:
1. 使用纯 HTTP API（Flask 服务器 + 另一个进程处理推理）
2. 使用支持 Android 原生的轻量模型（如 TensorFlow Lite）
"""

import os
import sys
import logging
import tempfile
import zipfile
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class ONNXRuntimeDownloadError(Exception):
    """下载/解压 ONNX Runtime AAR 失败。"""
    pass


class ONNXRuntimeInitError(Exception):
    """初始化 ONNX Runtime Java 环境失败。"""
    pass


# Maven 坐标
AAR_VERSION = "1.17.1"
MAVEN_URL = (
    f"https://repo1.maven.org/maven2/"
    f"com/microsoft/onnxruntime/onnxruntime-android/{AAR_VERSION}/"
    f"onnxruntime-android-{AAR_VERSION}.aar"
)


def download_and_extract_aar(
    dest_dir: Optional[str] = None,
    force: bool = False,
    arch: str = "arm64-v8a",
) -> str:
    """
    下载并解压 ONNX Runtime Android AAR，提取 .so 文件。

    AAR (Android Archive) 本质是 ZIP 文件，包含:
      - classes.jar         # Java 字节码
      - jni/<arch>/          # Native .so 库
      - AndroidManifest.xml  # Android 元数据
      - R.txt                # 资源 ID

    Returns:
        解压后文件所在目录
    """
    if dest_dir is None:
        dest_dir = tempfile.mkdtemp(prefix="onnxruntime_android_")

    aar_path = os.path.join(dest_dir, f"onnxruntime-android-{AAR_VERSION}.aar")

    # 检查是否已经存在
    jni_dir = os.path.join(dest_dir, "jni", arch)
    if not force and os.path.exists(jni_dir):
        sos = list(Path(jni_dir).rglob("libonnxruntime*.so"))
        if sos:
            logger.info(f"ONNX Runtime AAR 已就位: {jni_dir}")
            return dest_dir

    logger.info(f"下载 ONNX Runtime Android AAR {AAR_VERSION}...")

    # 下载
    try:
        import urllib.request
        urllib.request.urlretrieve(MAVEN_URL, aar_path)
        logger.info(f"下载完成: {aar_path}")
    except Exception as e:
        raise ONNXRuntimeDownloadError(f"下载失败: {e}")

    # 解压
    extract_dir = os.path.join(dest_dir, "extracted")
    if os.path.exists(extract_dir):
        shutil.rmtree(extract_dir)
    os.makedirs(extract_dir)

    try:
        with zipfile.ZipFile(aar_path, "r") as zf:
            zf.extractall(extract_dir)
        logger.info(f"AAR 解压完成: {extract_dir}")
    except Exception as e:
        raise ONNXRuntimeDownloadError(f"解压失败: {e}")

    # 复制 .so 文件到标准位置
    # 可能的路径: jni/<arch>/libonnxruntime.so 或 lib/<arch>/libonnxruntime.so
    jni_so = os.path.join(extract_dir, "jni", arch, "libonnxruntime.so")
    lib_so = os.path.join(extract_dir, "lib", arch, "libonnxruntime.so")

    if os.path.exists(jni_so):
        so_source = jni_so
    elif os.path.exists(lib_so):
        so_source = lib_so
    else:
        all_sos = list(Path(extract_dir).rglob("libonnxruntime*.so"))
        if all_sos:
            so_source = str(all_sos[0])
        else:
            raise ONNXRuntimeDownloadError(
                f"在 AAR 中未找到 libonnxruntime.so\n"
                f"解压目录内容: {os.listdir(extract_dir)}"
            )

    # 复制到目标位置
    os.makedirs(jni_dir, exist_ok=True)
    dest_so = os.path.join(jni_dir, "libonnxruntime.so")
    shutil.copy2(so_source, dest_so)
    logger.info(f"libonnxruntime.so 已提取: {dest_so}")

    return dest_dir


def setup_android_onnxruntime() -> Optional[str]:
    """
    设置 Android ONNX Runtime JNI 环境。

    Returns:
        ONNX Runtime .so 文件所在目录，失败返回 None
    """
    try:
        aar_dir = download_and_extract_aar()
        so_path = os.path.join(aar_dir, "jni", "arm64-v8a", "libonnxruntime.so")
        if not os.path.exists(so_path):
            # 搜索
            sos = list(Path(aar_dir).rglob("libonnxruntime*.so"))
            if sos:
                so_path = str(sos[0])
            else:
                return None

        from jnius import autoclass

        System = autoclass("java.lang.System")
        System.load(so_path)
        logger.info(f"ONNX Runtime JNI 加载成功: {so_path}")
        return aar_dir

    except ImportError:
        logger.error("pyjnius 未安装")
        return None
    except Exception as e:
        logger.error(f"设置 ONNX Runtime JNI 失败: {e}")
        return None


class AndroidONNXEngine:
    """
    基于 Android ONNX Runtime Java API 的推理引擎。

    通过 pyjnius 调用 Java ONNX Runtime (ai.onnxruntime 包)，
    接口与 SenseVoiceEngine 保持一致。

    ## ONNX Runtime Java API (ai.onnxruntime)

    核心类:
      OrtEnvironment     - 全局环境，单例
      OrtSession         - 推理会话
      OrtSessionOptions  - 会话配置（线程数、GPU 等）
      OnnxTensor         - Tensor 封装
      OrtUtil            - 工具类（数组/Tensor 转换）

    核心用法:
      env = OrtEnvironment.getEnvironment()
      options = OrtSessionOptions()
      options.setIntraOpNumThreads(2)
      session = env.createSession(modelPath, options)
      inputNames = session.getInputNames()  # Java List<String>
      floatArray = JavaArray.newArray("float", size)
      inputTensor = OnnxTensor.createTensor(env, floatArray)
      outputs = session.run(List<OnnxTensor>)  # Java List
      output = outputs.get(0)
      floatData = output.getFloatData()  # Java float[]
    """

    def __init__(
        self,
        model_dir: str = "models",
        num_threads: int = 2,
        use_gpu: bool = False,
    ):
        self.model_dir = model_dir
        self.num_threads = num_threads
        self.use_gpu = use_gpu
        self._is_loaded = False
        self._session = None
        self._env = None
        self._input_names: List[str] = []
        self._output_names: List[str] = []

    def load(self):
        """加载 ONNX 模型。"""
        from jnius import autoclass, JavaException

        model_path = self._find_model()
        if not model_path:
            raise FileNotFoundError(f"在 {self.model_dir} 中未找到 ONNX 模型")

        logger.info(f"使用 Android ONNX Runtime 加载: {model_path}")

        # 加载 JNI
        aar_dir = setup_android_onnxruntime()
        if not aar_dir:
            raise ONNXRuntimeInitError("无法加载 ONNX Runtime JNI 库")

        # 获取 Java 类
        # ai.onnxruntime 包的正确类名
        try:
            OrtEnvironment = autoclass("ai.onnxruntime.OrtEnvironment")
            OrtSession = autoclass("ai.onnxruntime.OrtSession")
            OrtSessionOptions = autoclass("ai.onnxruntime.OrtSessionOptions")
            OnnxTensor = autoclass("ai.onnxruntime.OnnxTensor")
            JavaArray = autoclass("java.lang.reflect.Array")
        except JavaException as e:
            raise ONNXRuntimeInitError(
                f"无法加载 ai.onnxruntime 类，请确认 onnxruntime-android AAR 已正确配置\n{e}"
            )

        # 创建环境（单例）
        self._env = OrtEnvironment.getEnvironment()

        # 创建会话选项
        options = OrtSessionOptions()
        options.setIntraOpNumThreads(self.num_threads)
        options.setInterOpNumThreads(self.num_threads)

        if self.use_gpu:
            try:
                options.addCUDA()
            except Exception:
                logger.warning("GPU 不可用，使用 CPU")

        # 创建推理会话
        self._session = self._env.createSession(model_path, options)

        # 获取输入输出名称
        input_names_java = self._session.getInputNames()
        output_names_java = self._session.getOutputNames()
        self._input_names = [input_names_java.get(i) for i in range(input_names_java.size())]
        self._output_names = [output_names_java.get(i) for i in range(output_names_java.size())]

        self._OnnxTensor = OnnxTensor
        self._JavaArray = JavaArray

        logger.info(f"模型加载成功: 输入={self._input_names}, 输出={self._output_names}")
        self._is_loaded = True

    def recognize(self, audio_pcm: bytes) -> str:
        """
        识别一段 PCM 音频。

        Args:
            audio_pcm: 16kHz 16bit 单声道 PCM 数据

        Returns:
            识别文本
        """
        if not self._is_loaded:
            raise RuntimeError("模型未加载，请先调用 load()")

        import numpy as np

        # 转换为 float32 [-1, 1]
        audio_np = np.frombuffer(audio_pcm, dtype=np.int16).astype(np.float32) / 32768.0

        # 准备 Java float[] 输入
        JavaArray = self._JavaArray
        OnnxTensor = self._OnnxTensor

        arr_len = len(audio_np)
        java_float_arr = JavaArray.newInstance(
            JavaArray.getComponentType(audio_np.__array_interface__["typestr"]),  # float
            arr_len
        )

        # 填充数组（pyjnius 的 Java 数组操作）
        for i, val in enumerate(audio_np):
            JavaArray.setFloat(None, java_float_arr, i, val)

        # 创建 OnnxTensor
        # createTensor(env, float[], shape)
        shape = JavaArray.newInstance("long", 1)
        JavaArray.setLong(None, shape, 0, arr_len)
        input_tensor = OnnxTensor.createTensor(self._env, java_float_arr, shape)

        # 运行推理
        outputs = self._session.run([])  # 空输入名称列表

        # 解析输出（具体格式取决于模型）
        if outputs and outputs.size() > 0:
            output_tensor = outputs.get(0)
            # 获取 float 数据并解码
            # 具体解码逻辑需要根据 SenseVoice 输出格式
            text = self._decode_onnx_output(output_tensor)
            input_tensor.close()
            return text

        return ""

    def _decode_onnx_output(self, output_tensor) -> str:
        """
        解码 ONNX 模型输出。

        具体实现取决于 SenseVoice 模型的输出结构。
        典型输出是 token IDs 序列，需要通过词汇表解码为文本。

        注意：这里返回空字符串占位，具体解码需要根据模型输出格式实现。
        """
        # output_tensor 是 OnnxTensor 对象
        # 获取原始数据:
        #   float_data = output_tensor.getFloatData()
        #   shape = output_tensor.getShape()
        # 然后根据具体模型格式解码
        return ""

    def _find_model(self) -> Optional[str]:
        """查找 ONNX 模型文件。"""
        model_dir = Path(self.model_dir)
        if not model_dir.exists():
            return None
        for pattern in ["*.onnx", "*.ORT.onnx"]:
            files = list(model_dir.glob(pattern))
            if files:
                return str(files[0])
        return None

    @property
    def is_loaded(self) -> bool:
        return self._is_loaded

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "engine": "onnxruntime-android",
            "loaded": self._is_loaded,
            "model_dir": self.model_dir,
            "threads": self.num_threads,
        }

    def unload(self):
        """卸载模型，释放资源。"""
        if self._session:
            try:
                self._session.close()
            except Exception:
                pass
        self._is_loaded = False
