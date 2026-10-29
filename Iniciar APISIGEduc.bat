@echo off
rem Inicia o servidor do APISIGEduc e abre o sistema no navegador.
rem Deixe esta janela aberta enquanto usa o sistema; para parar, feche-a ou aperte Ctrl+C.
chcp 65001 >nul
title APISIGEduc - servidor
cd /d "%~dp0"

if not exist ".venv\Scripts\apisigeduc.exe" (
    echo.
    echo O APISIGEduc nao esta instalado nesta pasta ^(falta .venv\Scripts\apisigeduc.exe^).
    echo Abra o PowerShell nesta pasta e rode:
    echo     .venv\Scripts\python.exe -m pip install -e ".[dev]"
    echo.
    pause
    exit /b 1
)

rem navegador visivel: a verificacao do login no SIGEduc e resolvida por uma pessoa
set SIGEDUC_HEADLESS=false

rem abre o navegador alguns segundos depois, quando o servidor ja estiver no ar
start "" /b cmd /c "timeout /t 4 /nobreak >nul & start "" http://127.0.0.1:8000"

".venv\Scripts\apisigeduc.exe" web
echo.
echo O servidor parou. Se apareceu um erro acima, envie um print dele.
pause
