#!/data/data/com.termux/files/usr/bin/bash
# ============================================================
# 粤语实时翻译工具 - 一键安装脚本
# 在 Termux 中运行:
#   chmod +x setup.sh && ./setup.sh
# ============================================================

set -e

# ---------- 颜色定义 ----------
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  粤语实时翻译工具 - 一键安装脚本${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# ---------- 检查 Termux 环境 ----------
if [ ! -d "/data/data/com.termux" ] && [ ! -f "/data/data/com.termux/files/usr/bin/pkg" ]; then
    echo -e "${YELLOW}[注意] 看起来不是在 Termux 环境中运行。${NC}"
    echo -e "${YELLOW}本工具专为 Termux (Android) 设计。${NC}"
    echo -e "${YELLOW}请确保你已从 F-Droid 安装了 Termux。${NC}"
    echo ""
    echo -e "${YELLOW}是否继续？(y/n)${NC}"
    read -r answer
    if [ "$answer" != "y" ]; then
        exit 0
    fi
fi

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

# ==========================================
# Step 1: 更新 Termux 包管理器
# ==========================================
echo -e "${GREEN}[1/6] 更新 Termux 软件源...${NC}"
pkg update -y && pkg upgrade -y
echo -e "${GREEN}  ✓ 完成${NC}"
echo ""

# ==========================================
# Step 2: 安装系统依赖
# ==========================================
echo -e "${GREEN}[2/6] 安装系统依赖...${NC}"
# Python,音频工具,构建工具,ffmpeg(音频格式转换)
pkg install -y python \
               build-essential \
               ffmpeg \
               termux-api \
               wget \
               cmake \
               pkg-config
echo -e "${GREEN}  ✓ 完成${NC}"
echo ""

# ==========================================
# Step 3: 验证 Termux:API 权限
# ==========================================
echo -e "${GREEN}[3/6] 验证 Termux:API 麦克风权限...${NC}"
if command -v termux-microphone-record &>/dev/null; then
    echo -e "${GREEN}  ✓ termux-microphone-record 可用${NC}"
else
    echo -e "${YELLOW}  ⚠ termux-microphone-record 未找到${NC}"
    echo -e "${YELLOW}  请确认已从 F-Droid 安装 Termux:API${NC}"
    echo -e "${YELLOW}  然后在手机设置中授予 Termux 麦克风权限${NC}"
fi
echo ""

# ==========================================
# Step 4: 安装 Python 依赖
# ==========================================
echo -e "${GREEN}[4/6] 安装 Python 依赖...${NC}"
# 先升级 pip
pip install --upgrade pip

# 安装核心依赖
# 注意: onnxruntime 在 Termux aarch64 上需要从源编译或使用社区包
# 先尝试标准 pip 安装
echo -e "${YELLOW}  正在安装 onnxruntime (可能需要较长时间)...${NC}"
if pip install onnxruntime 2>/dev/null; then
    echo -e "${GREEN}  ✓ onnxruntime 安装成功${NC}"
else
    echo -e "${YELLOW}  ⚠ pip 安装失败，尝试 Termux 社区包...${NC}"
    # Termux 社区的 onnxruntime 包
    pkg install -y onnxruntime 2>/dev/null && \
        echo -e "${GREEN}  ✓ onnxruntime (pkg) 安装成功${NC}" || \
        echo -e "${YELLOW}  ⚠ onnxruntime 安装有问题，后续可能需要单独处理${NC}"
fi

# 安装其余依赖
pip install -r requirements.txt
echo -e "${GREEN}  ✓ Python 依赖安装完成${NC}"
echo ""

# ==========================================
# Step 5: 下载 SenseVoice 模型
# ==========================================
echo -e "${GREEN}[5/6] 下载 SenseVoice Small 模型...${NC}"
chmod +x "$PROJECT_DIR/models/download_model.sh"
cd "$PROJECT_DIR/models"
bash download_model.sh
cd "$PROJECT_DIR"
echo -e "${GREEN}  ✓ 模型下载完成${NC}"
echo ""

# ==========================================
# Step 6: 验证安装
# ==========================================
echo -e "${GREEN}[6/6] 验证安装完整性...${NC}"
ERRORS=0

# 验证 Python 包
for pkg in flask flask_socketio numpy soundfile onnxruntime; do
    if python -c "import $pkg" 2>/dev/null; then
        echo -e "  ✓ Python 包: $pkg"
    else
        echo -e "  ${RED}✗ Python 包: $pkg (缺失)${NC}"
        ERRORS=$((ERRORS+1))
    fi
done

# 验证模型文件
if [ -f "$PROJECT_DIR/models/model.onnx" ]; then
    echo -e "  ✓ 模型文件: model.onnx ($(du -h "$PROJECT_DIR/models/model.onnx" | cut -f1))"
else
    echo -e "  ${RED}✗ 模型文件: model.onnx (缺失)${NC}"
    ERRORS=$((ERRORS+1))
fi

echo ""

if [ $ERRORS -eq 0 ]; then
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  安装成功！${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo -e "启动方式:"
    echo -e "  ${BLUE}cd ~/cantonese-translator${NC}"
    echo -e "  ${BLUE}python main.py${NC}"
    echo ""
    echo -e "然后在手机 Chrome 中打开:"
    echo -e "  ${BLUE}http://localhost:5000${NC}"
    echo ""
else
    echo -e "${RED}[警告] 有 $ERRORS 项安装不完整，请检查上方红色标记。${NC}"
    echo -e "${YELLOW}大多数功能仍可使用，但某些依赖可能影响准确率。${NC}"
fi
