@echo off
chcp 65001 >nul
title Claude Code RP Setup
echo.
echo   Launching Claude Code environment check...
echo   If a security prompt appears, select "Yes" or "Allow"
echo.
powershell -ExecutionPolicy Bypass -File "%~dp0setup-claude-code.ps1"
pause
