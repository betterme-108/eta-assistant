@echo off
chcp 65001 >nul
REM ============================================================
REM 英语教学助手 English Teaching Assistant 一键启动脚本（Windows）
REM 用法：双击 start.bat 或在命令行执行
REM 首次运行自动完成：检测/安装 Python → 创建虚拟环境 → 安装依赖
REM 国内网络可先执行 set PIP_MIRROR=1 再用清华镜像装依赖
REM
REM 依赖安装位置（不污染系统）：
REM   · 项目依赖全部装在项目目录内的独立虚拟环境 .venv\ 里，
REM     不写入系统 Python 路径；卸载本系统 = 删除整个项目目录
REM   · 仅当电脑上没有 Python 时，才通过 winget 装到当前用户目录
REM     （%LOCALAPPDATA%，非系统目录）——这是唯一会装在项目外的东西
REM ============================================================
cd /d %~dp0

echo ══════════ 英语教学助手 English Teaching Assistant ══════════

REM ---- 0/3 Python 运行时（没有则尝试用 winget 自动安装）----
set "PYCMD=python"
where python >nul 2>nul
if not errorlevel 1 goto :python_ok
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
  set "PYCMD=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  goto :python_ok
)
echo [0/3] 未检测到 Python，尝试通过 winget 自动安装（需联网，约 2~5 分钟）...
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
if errorlevel 1 (
  echo   自动安装失败（Windows 10 1809 以下没有 winget）。请到 https://www.python.org/downloads/
  echo   下载安装 Python 3.12，安装时务必勾选 "Add python.exe to PATH"，装好后重新双击本脚本。
  pause
  exit /b 1
)
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
  set "PYCMD=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  goto :python_ok
)
echo   winget 安装完成但未找到 Python 目录，请新开一个命令行窗口后重新运行本脚本。
pause
exit /b 1

:python_ok
echo [0/3] Python 运行时 OK

REM ---- 1/3 虚拟环境（依赖装在项目内独立目录 .venv\，不写系统路径） ----
if not exist .venv\Scripts\python.exe (
  echo [1/3] 首次运行：创建虚拟环境并安装依赖，请稍候...
  echo        依赖安装在项目内独立目录 .venv\（不写入系统路径）
  "%PYCMD%" -m venv .venv
  if "%PIP_MIRROR%"=="1" (
    .venv\Scripts\pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
  ) else (
    .venv\Scripts\pip install -q -r requirements.txt
  )
) else (
  echo [1/3] 虚拟环境 OK（依赖装在项目内 .venv\，不写入系统路径）
)

REM ---- 2/3 配置文件 ----
if not exist .env (
  echo [2/3] 未找到 .env 配置文件
  echo.
  echo   请先复制模板并填入 key：
  echo     copy .env.example .env
  echo   需要填写 DEEPSEEK_API_KEY 与 MINERU_TOKEN
  echo.
  pause
  exit /b 1
)
echo [2/3] 配置文件 .env OK

REM ---- 3/3 启动 ----
echo [3/3] 启动服务：http://127.0.0.1:8000 ...
start "" "http://127.0.0.1:8000"
.venv\Scripts\uvicorn app.main:app --port 8000
pause
