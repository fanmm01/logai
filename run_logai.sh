#!/usr/bin/env bash
set -uo pipefail

echo "============================================"
echo "  LogAI 4.3.4 - TRPG Log Analysis Server"
echo "============================================"
echo ""

# ============================================================
#  配置区域
# ============================================================
#可以将使用的AI API令牌替换下面的"sk-………"。切勿将此令牌透露给其他人！
#一种替代方案见README。推荐彼替代方案，因为它可以让你在不修改此文件的情况下安全地使用环境变量来存储敏感信息。
CFG_API_KEY="sk-xxxxxxxxxxxxxxxxxxxxc"
#使用的AI的相关信息，包括base url与ai模型名称
CFG_API_BASE_URL="https://api.deepseek.com"
CFG_AI_MODEL="deepseek-v4-flash"
CFG_AI_MODEL_PRO="deepseek-v4-pro"
#文生图AI的API令牌。同样切勿透露！
CFG_IMAGE_API_KEY="pst-zzzzzzzzzzzzzzzzzzzz"

#以下为Napcat的http/ws连接url及token。请按需修改。
CFG_NAPCAT_URL="http://127.0.0.1:8084"
CFG_NAPCAT_TOKEN="1"
CFG_WS_URL="ws://127.0.0.1:3001"
CFG_WS_TOKEN=""

CFG_HOST="0.0.0.0"
#logai后端运行的端口号。如提示端口被占用请修改。但是这是不建议的行动；此处若进行了修改，则需要把前端配置进行同样的修改。
CFG_PORT="8000"
#以下可忽略，为内部参数
CFG_BRIDGE_TOKEN=""
CFG_BRIDGE_PUBLIC_BASE=""
CFG_WS_ENABLED="1"
CFG_BRIDGE_MODE="0"
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_DIR="$SCRIPT_DIR/python"
PYTHON_EXE=""

# ---- 用哪个 python-build-standalone release tag ------------
PBS_TAG="20260211"
PBS_REPO="astral-sh/python-build-standalone"

USE_MIRROR=1
MIRROR_URLS=(
    "https://gitee.com/masx200/python-build-standalone/releases/download"
    "https://ghproxy.cc/https://github.com/${PBS_REPO}/releases/download"
    "https://mirror.ghproxy.com/https://github.com/${PBS_REPO}/releases/download"
)

build_base_url() {
    local tag="$1"
    if [[ "$USE_MIRROR" == "1" ]]; then
        for m in "${MIRROR_URLS[@]}"; do
            echo "$m/$tag"
            return
        done
    fi
    echo "https://github.com/${PBS_REPO}/releases/download/$tag"
}

# ---- 检测 OS / Arch ----
OS_TYPE="$(uname -s)"
ARCH="$(uname -m)"
case "$ARCH" in
    x86_64)  PBS_ARCH="x86_64"   ;;
    amd64)   PBS_ARCH="x86_64"   ;;
    aarch64) PBS_ARCH="aarch64"  ;;
    arm64)   PBS_ARCH="aarch64"  ;;
    i386|i686) PBS_ARCH="i686"   ;;
    *)
        echo "[ERROR] 不支持的 CPU 架构: $ARCH"
        exit 1
        ;;
esac

# ---- 仅检测本地便携 Python ----
detect_local_python() {
    if [ -x "$PYTHON_DIR/bin/python3" ]; then
        PYTHON_EXE="$PYTHON_DIR/bin/python3"
        echo "[OK] 使用本地 Python: $PYTHON_EXE"
        return 0
    fi
    return 1
}

# ---- 下载函数 ----
download_file() {
    local url="$1"
    local outfile="$2"
    if [[ "$USE_MIRROR" == "1" ]]; then
        for m in "${MIRROR_URLS[@]}"; do
            local prefix="https://github.com/${PBS_REPO}/releases/download"
            local final_url="${url/#$prefix/}"
            final_url="${m}${final_url}"
            echo "[INFO] 尝试镜像下载: $(echo "$final_url" | sed 's|https://[^/]*/||')"
            if curl -fSL --connect-timeout 15 --max-time 120 -o "$outfile" "$final_url" 2>/dev/null; then
                if [ -s "$outfile" ]; then return 0; fi
            fi
        done
    fi
    echo "[INFO] 直连下载: $url"
    if command -v curl &>/dev/null; then
        curl -fSL --connect-timeout 15 --max-time 180 -o "$outfile" "$url"
    elif command -v wget &>/dev/null; then
        wget --timeout=15 --tries=2 -q -O "$outfile" "$url"
    else
        echo "[ERROR] 需要 curl 或 wget"
        return 1
    fi
}

install_portable_python() {
    echo "[INFO] 未检测到本地便携 Python，开始下载..."
    echo "[INFO] 目标: $OS_TYPE / $PBS_ARCH"

    local BASE_URL="https://github.com/${PBS_REPO}/releases/download/${PBS_TAG}"
    local PBS_FILENAME=""
    case "$OS_TYPE" in
        Linux)
            PBS_FILENAME="cpython-3.11.14+${PBS_TAG}-${PBS_ARCH}-unknown-linux-gnu-install_only.tar.gz"
            ;;
        Darwin)
            PBS_FILENAME="cpython-3.11.14+${PBS_TAG}-${PBS_ARCH}-apple-darwin-install_only.tar.gz"
            ;;
        *)
            echo "[ERROR] 暂不支持操作系统: $OS_TYPE"
            exit 1
            ;;
    esac

    local TAR_URL="${BASE_URL}/${PBS_FILENAME}"
    local TAR_FILE="$SCRIPT_DIR/_tmp_python_install.tar.gz"

    mkdir -p "$PYTHON_DIR"
    mkdir -p "$(dirname "$TAR_FILE")"

    echo "[INFO] 下载: $PBS_FILENAME"
    echo "[INFO] 来源: $TAR_URL"
    echo ""

    if ! download_file "$TAR_URL" "$TAR_FILE"; then
        echo "[ERROR] ❌ 下载失败，请手动下载后重试。"
        echo "手动下载地址: $TAR_URL"
        rm -f "$TAR_FILE"
        exit 1
    fi

    echo "[INFO] 解压到 $PYTHON_DIR ..."
    tar -xzf "$TAR_FILE" -C "$PYTHON_DIR" --strip-components=1
    rm -f "$TAR_FILE"

    if [ ! -x "$PYTHON_DIR/bin/python3" ]; then
        echo "[ERROR] Python 解压失败"
        exit 1
    fi

    PYTHON_EXE="$PYTHON_DIR/bin/python3"
    chmod +x "$PYTHON_EXE" 2>/dev/null || true
    echo "[OK] ✅ 便携 Python 已就绪"
}

# ---- MAIN ----
if ! detect_local_python; then
    install_portable_python
fi

echo "[INFO] Python: $($PYTHON_EXE --version 2>&1)"
echo ""

echo "[INFO] 检查依赖包..."
$PYTHON_EXE -m ensurepip --upgrade 2>/dev/null || true

PIP_INDEX=""
if [[ "$USE_MIRROR" == "1" ]]; then
    PIP_INDEX="--index-url https://pypi.tuna.tsinghua.edu.cn/simple/"
fi

$PYTHON_EXE -m pip install --quiet --disable-pip-version-check \
    ${PIP_INDEX} \
    flask requests pillow openai python-docx PyPDF2 pymupdf websockets \
    2>&1 | grep -v "already satisfied" || true

echo "[OK] 依赖就绪"
echo ""

CLI_ARGS=()
[ -n "$CFG_API_KEY" ]            && CLI_ARGS+=(--api-key "$CFG_API_KEY")
[ -n "$CFG_API_BASE_URL" ]       && CLI_ARGS+=(--api-base-url "$CFG_API_BASE_URL")
[ -n "$CFG_AI_MODEL" ]           && CLI_ARGS+=(--ai-model "$CFG_AI_MODEL")
[ -n "$CFG_AI_MODEL_PRO" ]      && CLI_ARGS+=(--ai-model-pro "$CFG_AI_MODEL_PRO")
[ -n "$CFG_IMAGE_API_KEY" ]     && CLI_ARGS+=(--image-api-key "$CFG_IMAGE_API_KEY")
[ -n "$CFG_HOST" ]              && CLI_ARGS+=(--host "$CFG_HOST")
[ -n "$CFG_PORT" ]              && CLI_ARGS+=(--port "$CFG_PORT")
[ -n "$CFG_NAPCAT_URL" ]        && CLI_ARGS+=(--napcat-url "$CFG_NAPCAT_URL")
[ -n "$CFG_NAPCAT_TOKEN" ]      && CLI_ARGS+=(--napcat-token "$CFG_NAPCAT_TOKEN")
[ -n "$CFG_WS_URL" ]            && CLI_ARGS+=(--ws-url "$CFG_WS_URL")
[ -n "$CFG_WS_TOKEN" ]          && CLI_ARGS+=(--ws-token "$CFG_WS_TOKEN")
[ -n "$CFG_BRIDGE_TOKEN" ]      && CLI_ARGS+=(--bridge-token "$CFG_BRIDGE_TOKEN")
[ -n "$CFG_BRIDGE_PUBLIC_BASE" ]&& CLI_ARGS+=(--bridge-public-base "$CFG_BRIDGE_PUBLIC_BASE")
[ -n "$CFG_WS_ENABLED" ]        && CLI_ARGS+=(--ws-enabled "$CFG_WS_ENABLED")
[ -n "$CFG_BRIDGE_MODE" ]       && CLI_ARGS+=(--bridge-mode "$CFG_BRIDGE_MODE")

echo "[INFO] 启动 LogAI 服务器..."
echo ""
cd "$SCRIPT_DIR"
exec "$PYTHON_EXE" logai_server_release.py "${CLI_ARGS[@]}" "$@"
