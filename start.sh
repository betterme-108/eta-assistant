#!/bin/bash
# ============================================================
# 英语教学助手 English Teaching Assistant · 一键启动脚本（macOS / Linux）
# 用法：./start.sh          （默认端口 8000，就绪后自动开浏览器）
#       PORT=9000 ./start.sh（自定义端口）
#       PIP_MIRROR=1 ./start.sh（首次装依赖用清华镜像加速，国内网络推荐）
#
# 首次运行自动完成：检测/安装 Python → 创建虚拟环境 → 安装依赖（约 1~3 分钟）
#
# 依赖安装位置（不污染系统）：
#   · 项目依赖（FastAPI/uvicorn 等）全部装在项目目录内的独立虚拟环境
#     .venv/ 里，不写入系统 Python 路径；卸载本系统 = 删除整个项目目录
#   · 仅当电脑上没有 Python 运行时，才允许往电脑上装一份 Python
#     （Homebrew/apt，属于运行必需的基础环境），这是唯一会装在项目外的东西
# ============================================================
set -e
cd "$(dirname "$0")"
PORT="${PORT:-8000}"

# 端口校验：1024 以下是系统保留的特权端口，普通用户无权绑定（会报 permission denied）
if [ "$PORT" -lt 1024 ] 2>/dev/null; then
  echo "[✗] 端口 $PORT 不可用：1024 以下是系统保留端口，普通用户无权绑定"
  echo ""
  echo "  请换一个 1024 以上的端口，例如："
  echo "    PORT=8011 ./start.sh"
  exit 1
fi

echo "══════════ 英语教学助手 English Teaching Assistant ══════════"

# ---- 0/4 Python 运行时（没有则尝试自动安装）----
# 依次探测：PATH 里的 python3 → Homebrew 常见路径 → python.org 安装包路径
# 探测必须“真跑一次”验证：macOS 的 /usr/bin/python3 是占位 shim，
# 未装命令行开发者工具时虽然存在但跑不起来，直接选用会导致后续失败
PYTHON=""
for cand in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 \
            /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
            /Library/Frameworks/Python.framework/Versions/3.11/bin/python3; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'print(1)' >/dev/null 2>&1; then PYTHON="$cand"; break; fi
done
if [ -z "$PYTHON" ]; then
  echo "[0/4] 未检测到 Python 3，尝试自动安装（需联网，约 1~3 分钟）..."
  case "$(uname -s)" in
    Darwin)
      if command -v brew >/dev/null 2>&1; then
        brew install python@3.12
      else
        echo "  ✗ 未找到 Homebrew，无法自动安装。请二选一："
        echo "    1. 安装 Homebrew：/bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
        echo "    2. 官网安装 Python 3：https://www.python.org/downloads/"
        echo "    装好后重新运行 ./start.sh 即可"
        exit 1
      fi
      ;;
    Linux)
      sudo apt-get update -y && sudo apt-get install -y python3 python3-venv python3-pip
      ;;
    *)
      echo "  ✗ 无法识别的系统，请手动安装 Python 3 后重新运行本脚本"
      exit 1
      ;;
  esac
  # 安装后重新探测（brew/sudo 安装可能未刷新当前终端 PATH）
  for cand in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'print(1)' >/dev/null 2>&1; then PYTHON="$cand"; break; fi
  done
  if [ -z "$PYTHON" ]; then
    echo "  ✗ 安装后仍找不到 Python，请新开终端后重新运行"
    exit 1
  fi
fi

# ---- 1/4 虚拟环境（不存在或不可用则自动重建并安装依赖）----
# 依赖统一装在项目内独立目录 .venv/（不写系统路径，见文件头说明）
# 国内网络可加 PIP_MIRROR=1 用清华镜像加速依赖下载
# 注意：.venv 里写死了绝对路径（python3 软链指向系统 Python，pip/uvicorn
# 等脚本的 shebang 指向本机 .venv 位置），整包拷贝到其他电脑后必然失效。
# 因此这里做“功能自检”：真跑一次 python 与 pip，跑不通就整个删除重建，
# 不要试图复用已损坏的 .venv
# 版本显示：实际运行的是 .venv 里的 Python（自检/启动全走它）；
# 系统 Python 仅在需要（重）建虚拟环境时才用到，避免版本显示误导
PIP_ARGS=""
[ "${PIP_MIRROR:-}" = "1" ] && PIP_ARGS="-i https://pypi.tuna.tsinghua.edu.cn/simple"
if [ -x ".venv/bin/python" ] \
   && .venv/bin/python -c 'import sys' >/dev/null 2>&1 \
   && .venv/bin/pip --version >/dev/null 2>&1 \
   && [ -x ".venv/bin/uvicorn" ]; then
  echo "[0/4] Python 运行时 ✓（$(.venv/bin/python --version 2>&1)，项目虚拟环境）"
  echo "[1/4] 虚拟环境 ✓（依赖装在项目内 .venv/，不写入系统路径）"
else
  echo "[0/4] Python 运行时 ✓（$($PYTHON --version)，将用于创建虚拟环境）"
  echo "[1/4] 虚拟环境不可用（首次运行 / 已损坏 / 从其他电脑拷贝而来），重建并安装依赖（约 1 分钟）..."
  echo "       依赖安装在项目内独立目录 .venv/（不写入系统路径）"
  rm -rf .venv
  "$PYTHON" -m venv .venv
  .venv/bin/pip install -q $PIP_ARGS -r requirements.txt
fi

# ---- 2/4 配置文件 ----
if [ ! -f .env ]; then
  echo "[2/4] ✗ 未找到 .env 配置文件"
  echo ""
  echo "  请先复制模板并填入 key："
  echo "    cp .env.example .env"
  echo ""
  echo "  需要填写："
  echo "    DEEPSEEK_API_KEY  主观题判定/作文评分/归因与练习生成（https://platform.deepseek.com）"
  echo "    MINERU_TOKEN      拍照识别（https://mineru.net/apiManage/token，14 天有效）"
  exit 1
fi
echo "[2/4] 配置文件 .env ✓"

# ---- 3/4 环境自检（快速跑一遍测试套件）----
echo "[3/4] 环境自检..."
if .venv/bin/python -m pytest tests/ -q >/dev/null 2>&1; then
  echo "       自检通过 ✓"
else
  echo "       ⚠ 自检未全部通过，仍继续启动（可稍后执行 .venv/bin/python -m pytest tests/ -q 查看）"
fi

# ---- 4/4 启动服务 ----
if lsof -ti:"$PORT" >/dev/null 2>&1; then
  echo "[4/4] 端口 $PORT 已被占用，先停止旧服务..."
  lsof -ti:"$PORT" | xargs kill 2>/dev/null || true
  sleep 1
fi
echo "[4/4] 启动服务：http://127.0.0.1:$PORT ..."
# --no-access-log：访问日志由应用内中间件统一输出（含耗时），避免重复
.venv/bin/uvicorn app.main:app --port "$PORT" --no-access-log &
UVPID=$!

# 等待服务就绪（最多 10 秒）
READY=0
for _ in $(seq 1 20); do
  sleep 0.5
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/api/health"; then READY=1; break; fi
done

echo ""
if [ "$READY" = "1" ]; then
  echo "══════════ 启动成功 ══════════"
  echo "  系统地址：  http://127.0.0.1:$PORT"
  echo "  健康检查：  http://127.0.0.1:$PORT/api/health"
  echo "  运行日志：  runtime/logs/eta.log（按天保存、保留 14 天；终端同步输出）"
  echo "  演示数据：  .venv/bin/python scripts/seed_full.py --reset"
  echo "  停止服务：  Ctrl + C"
  echo "══════════════════════════════"
  # macOS 自动打开浏览器（Linux 桌面可换成 xdg-open）
  command -v open >/dev/null 2>&1 && open "http://127.0.0.1:$PORT" 2>/dev/null || true
else
  echo "⚠ 服务未在 10 秒内就绪，请查看上方日志排查"
fi

wait $UVPID
