#!/data/data/com.termux/files/usr/bin/bash
# ============================================================
# run.sh — 粤语实时翻译 一键启动脚本
# ============================================================
# 在 Termux 中运行:
#   chmod +x run.sh && ./run.sh
# ============================================================

set -e

# ---------- 颜色 ----------
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

echo ""
echo -e "${BLUE}======================================================${NC}"
echo -e "${BLUE}   粤语实时翻译 - Cantonese Real-Time Translator${NC}"
echo -e "${BLUE}======================================================${NC}"
echo ""

# ---------- 1. 检查 Python ----------
echo -e "${CYAN}[1/4] 检查 Python...${NC}"
if ! command -v python &>/dev/null; then
    echo -e "${RED}  ✗ Python 未安装！请先运行: pkg install python${NC}"
    exit 1
fi
echo -e "${GREEN}  ✓ Python $(python --version 2>&1 | cut -d' ' -f2)${NC}"

# ---------- 2. 检查依赖 ----------
echo -e "${CYAN}[2/4] 检查 Python 依赖...${NC}"
MISSING_PKGS=()
for pkg in flask onnxruntime numpy; do
    if ! python -c "import $pkg" 2>/dev/null; then
        MISSING_PKGS+=("$pkg")
    fi
done

if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
    echo -e "${YELLOW}  ⚠ 缺少依赖: ${MISSING_PKGS[*]}${NC}"
    echo -e "${YELLOW}  正在安装...${NC}"
    pip install -r requirements.txt
    echo -e "${GREEN}  ✓ 依赖安装完成${NC}"
else
    echo -e "${GREEN}  ✓ 所有依赖已就绪${NC}"
fi

# ---------- 3. 检查模型文件 ----------
echo -e "${CYAN}[3/4] 检查 ASR 模型...${NC}"
MODEL_PATH="$PROJECT_DIR/models/model.onnx"
TOKENS_PATH="$PROJECT_DIR/models/tokens.txt"

if [ ! -f "$MODEL_PATH" ] || [ ! -f "$TOKENS_PATH" ]; then
    echo -e "${YELLOW}  ⚠ 模型文件不完整${NC}"
    echo -e "${YELLOW}  正在下载模型 (~200MB)...${NC}"
    cd "$PROJECT_DIR/models"
    bash download_model.sh
    cd "$PROJECT_DIR"
    echo -e "${GREEN}  ✓ 模型下载完成${NC}"
else
    MODEL_SIZE=$(du -h "$MODEL_PATH" | cut -f1)
    echo -e "${GREEN}  ✓ 模型已就绪 ($MODEL_SIZE)${NC}"
fi

# ---------- 4. 启动 ----------
echo -e "${CYAN}[4/4] 启动服务...${NC}"
echo ""
echo -e "${GREEN}======================================================${NC}"
echo -e "${GREEN}  ✅ 服务已启动！${NC}"
echo -e "${GREEN}======================================================${NC}"
echo ""
echo -e "  ${YELLOW}打开手机 Chrome，访问:${NC}"
echo -e "  ${BLUE}http://127.0.0.1:5000${NC}"
echo ""
echo -e "  ${YELLOW}快捷键:${NC}"
echo -e "    空格键 = 开始/停止录音"
echo -e "    Esc    = 清空结果"
echo -e "    Ctrl+C = 退出程序"
echo ""
echo -e "${YELLOW}  确保已授予 Termux 麦克风权限！${NC}"
echo ""
echo -e "${BLUE}------------------------------------------------------${NC}"
echo ""

# 启动主程序
exec python main.py --host 127.0.0.1 --port 5000
