@echo off
rem CEA Bot 启动脚本（Windows）。三件事：
rem   1. 切到脚本自身所在目录，不依赖调用方 cwd；
rem   2. 自检 .venv 与项目是否已安装：缺失时给可执行的安装提示并以非 0 退出，不继续往下跑；
rem   3. 以模块方式启动：.venv\Scripts\python -m cea_bot
rem 控制台码页临时切到 UTF-8 以便正确显示中文提示，退出前恢复原码页。
setlocal
cd /d "%~dp0"

for /f "tokens=2 delims=:" %%c in ('chcp') do set "OLDCP=%%c"
chcp 65001 >nul

set "PY=.venv\Scripts\python.exe"
set "HINT=.venv\Scripts\python -m pip install -e ".[dev]" --index-url https://pypi.org/simple"

if not exist "%PY%" goto :no_venv

rem src-layout 下"未安装"等于什么都 import 不到；先自检，避免直接甩出裸 traceback。
"%PY%" -c "import cea, cea_bot, cea_bot.handlers.basic, aiohttp, yaml" >nul 2>&1
if errorlevel 1 goto :not_installed

"%PY%" -m cea_bot
set "RC=%errorlevel%"
goto :restore_cp

:no_venv
echo [启动失败] 未找到虚拟环境解释器：%PY%
echo   请先在仓库根创建虚拟环境并安装依赖：
echo     python -m venv .venv
echo     %HINT%
pause
set "RC=1"
goto :restore_cp

:not_installed
echo [启动失败] 当前解释器尚未安装本项目：%PY%
echo   清华镜像暂缺 qq-botpy，安装必须显式指定官方源：
echo     %HINT%
echo   复现自检：.venv\Scripts\python -c "import cea, cea_bot, cea_bot.handlers.basic, aiohttp, yaml"
pause
set "RC=1"

:restore_cp
if defined OLDCP chcp%OLDCP% >nul
exit /b %RC%
