#!/usr/bin/env bash
# CEA Bot 启动脚本（Ubuntu / POSIX）。与 start.bat 的三件事逐一对齐：
#   1. 切到脚本自身所在目录，不依赖调用方 cwd；
#   2. 自检 .venv/bin/python 存在、且项目已安装：失败时给**可执行的**安装提示并以非 0 退出；
#   3. 以模块方式启动：.venv/bin/python -m cea_bot
# 刻意只做这三件事：**不做 systemd / 守护进程 / 开机自启**（进程管理交给部署者自己决定）。
# 首次部署（Python ≥ 3.13、CJK 字体、NapCat 同机、时区）见 README「8. 在 Ubuntu 上部署」。

# —— 守卫（放在 set -u 之前：它不依赖任何未赋值变量，也不会把选项泄漏出去）——
# 守卫一：本脚本必须是 bash 在跑。**这一步的写法必须对 dash 安全**：Ubuntu 的 /bin/sh 是 dash，
#    它不认识 `${BASH_SOURCE[0]}` 这类数组下标，会直接报 `Bad substitution` 并以 2 退出 ——
#    比"一句能读懂的提示"糟得多。这里只用 `[ -z "${VAR:-}" ]`，dash 也能解析到下一行提示。
if [ -z "${BASH_VERSION:-}" ]; then
  echo "[启动失败] 本脚本需要 bash，当前 shell（${0##*/}）不是 —— Ubuntu 的 /bin/sh 是 dash"
  echo "  请用：./start.sh 或 bash start.sh（不要用 sh start.sh）"
  return 1 2>/dev/null || exit 1
fi

# 守卫二：本脚本必须"被执行"，而不是被 source、也不是从 stdin 喂给 bash。两种情况都会让
#    cd 变成 no-op、set -u 泄漏进调用方 shell、exec 把调用方 shell 直接替换成 bot 进程：
#      · `source ./start.sh`：$0 仍是调用方 shell，BASH_SOURCE[0] 才是本脚本；
#      · `bash < start.sh` ：BASH_SOURCE[0] **未设置**（下面用 `:-` 兜成空串），$0 是 `bash`。
if [ "${BASH_SOURCE[0]:-}" != "$0" ]; then
  echo "[启动失败] 请直接执行本脚本（./start.sh 或 bash start.sh），不要 source 它，也不要用 bash < start.sh"
  # 中止手段必须两种场景都正确：被 source 时 return 收回调用方脚本，被执行/被 stdin 喂入时
  # return 会失败 —— 此时必须 exit。只写 `return 1` 会让 `bash < start.sh` **继续往下执行**
  # （fail-open：仓库里若有 .venv/bin/python，它会真的把 bot 拉起来）。
  return 1 2>/dev/null || exit 1
fi
set -u

# ① 切到脚本自身所在目录：否则从别处调用时，.venv 与仓库根的 config.yaml 都找错地方。
# cd 失败必须立刻退出 —— 在错误的 cwd 上继续，只会得到一个更难看懂的报错。
cd "$(dirname "$0")" || exit 1

PY=".venv/bin/python"
# 清华镜像暂缺 qq-botpy，安装**必须**显式指定官方源（与 start.bat 同一条命令）。
HINT='.venv/bin/python -m pip install -e ".[dev]" --index-url https://pypi.org/simple'

# ② 自检一：解释器存在。venv 未创建（或建在别的解释器下）时，直接给出下一步。
if [ ! -x "$PY" ]; then
  echo "[启动失败] 未找到虚拟环境解释器：$PY"
  echo "  请先在仓库根创建虚拟环境并安装依赖（需 Python ≥ 3.13；Ubuntu 24.04 自带的是 3.12）："
  echo "    python3.13 -m venv .venv"
  echo "    $HINT"
  exit 1
fi

# ② 自检二：项目已安装。src-layout 下"未安装"等于什么都 import 不到，先自检再启动，
# 避免直接甩出一段裸 traceback（自检输出与 start.bat 一样不展示，复现命令见下行提示）。
if ! "$PY" -c "import cea, cea_bot, cea_bot.handlers.basic, aiohttp, yaml" >/dev/null 2>&1; then
  echo "[启动失败] 当前解释器尚未安装本项目：$PY"
  echo "  清华镜像暂缺 qq-botpy，安装必须显式指定官方源："
  echo "    $HINT"
  echo '  复现自检：.venv/bin/python -c "import cea, cea_bot, cea_bot.handlers.basic, aiohttp, yaml"'
  exit 1
fi

# ③ 启动。exec 替换本 shell：Ctrl+C 直达 bot 进程，退出码原样透传给调用方。
exec "$PY" -m cea_bot
