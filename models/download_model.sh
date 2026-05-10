#!/data/data/com.termux/files/usr/bin/bash
# ============================================================
# 下载 SenseVoice Small 模型（支持粤语 + 简体中文输出）
# 本脚本在 Termux 环境中运行
# ============================================================

set -e

MODEL_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$MODEL_DIR"

echo "========================================"
echo "  SenseVoice Small 模型下载"
echo "  模型来源: ModelScope / HuggingFace"
echo "========================================"
echo ""

# ---------- 检测 wget 或 curl ----------
DOWNLOAD_CMD=""
if command -v wget &>/dev/null; then
    DOWNLOAD_CMD="wget --continue --show-progress -q"
elif command -v curl &>/dev/null; then
    DOWNLOAD_CMD="curl -L -# -O"
else
    echo "[错误] 未找到 wget 或 curl，请先安装:"
    echo "  pkg install wget"
    exit 1
fi

# ---------- 下载 SenseVoice Small (ONNX) ----------
# 使用 ModelScope 镜像（国内速度更快）
BASE_URL="https://modelscope.cn/api/v1/models/iic/SenseVoiceSmall/repo?Revision=master&FilePath="

# SenseVoice Small ONNX 文件清单
declare -A FILES
FILES["model.onnx"]="${BASE_URL}model.onnx"
FILES["config.yaml"]="${BASE_URL}config.yaml"
FILES["am.mvn"]="${BASE_URL}am.mvn"
FILES["tokens.txt"]="${BASE_URL}tokens.txt"
FILES["se_dict.txt"]="${BASE_URL}se_dict.txt"

echo ""
echo ">>> 正在从 ModelScope 下载 SenseVoice Small 模型 (~200MB)..."
echo ""

for name in "${!FILES[@]}"; do
    url="${FILES[$name]}"
    if [ -f "$name" ] && [ -s "$name" ]; then
        echo "  [已存在] $name (跳过)"
    else
        echo "  [下载中] $name ..."
        if echo "$DOWNLOAD_CMD" | grep -q wget; then
            wget --continue --show-progress -q -O "$name" "$url"
        else
            curl -L -# -o "$name" "$url"
        fi
        echo "  [完成] $name"
    fi
done

echo ""
echo "========================================"
echo "  模型下载完毕！"
echo "  文件位置: $MODEL_DIR"
ls -lh *.onnx *.yaml *.txt *.mvn 2>/dev/null || true
echo "========================================"

# ---------- 提示后续步骤 ----------
echo ""
echo "下一步:"
echo "  1. 返回项目根目录: cd .."
echo "  2. 安装 Python 依赖: pip install -r requirements.txt"
echo "  3. 启动翻译: python main.py"
echo ""
