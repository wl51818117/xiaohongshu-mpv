@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

echo ============================================
echo   电商工作台 MVP · 一键启动
echo ============================================
echo.

REM ── 1. 检查 Python ──
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 python，请先安装 Python 3.11+
    pause
    exit /b 1
)
echo [1/4] Python: OK

REM ── 2. 准备虚拟环境 ──
if not exist ".venv\Scripts\python.exe" (
    echo [2/4] 创建虚拟环境...
    python -m venv .venv
    if errorlevel 1 (
        echo [错误] 虚拟环境创建失败
        pause
        exit /b 1
    )
) else (
    echo [2/4] 虚拟环境: 已存在
)

REM ── 3. 安装依赖 ──
echo [3/4] 安装依赖...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络
    pause
    exit /b 1
)
echo [3/4] 依赖: OK

REM ── 4. 启动服务 ──
echo [4/4] 启动服务...
echo.
echo   接口文档:  http://127.0.0.1:8000/docs
echo   状态检查:  http://127.0.0.1:8000/api/pipeline/status
echo   按 Ctrl+C 停止
echo.
".venv\Scripts\python.exe" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

endlocal
