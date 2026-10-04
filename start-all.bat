@echo off
chcp 65001 >nul
setlocal

REM ============================================
REM   电商工作台 · 前后端一键启动
REM ============================================

cd /d "%~dp0"

echo.
echo [1/2] 启动后端 (FastAPI :8000)...
start "工作台-后端" cmd /k ^
  "cd /d "%~dp0backend" && .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

REM 等待后端起来
timeout /t 4 /nobreak >nul

echo [2/2] 启动前端 (Vite :5173)...
start "工作台-前端" cmd /k ^
  "cd /d "%~dp0frontend" && npm run dev"

echo.
echo ============================================
echo   前端界面:  http://127.0.0.1:5173
echo   接口文档:  http://127.0.0.1:8000/docs
echo.
echo   关闭方式: 关掉弹出的两个黑窗口
echo ============================================
echo.

timeout /t 6 /nobreak >nul
start http://127.0.0.1:5173

endlocal
