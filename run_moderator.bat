@echo off
rem Запуск бота-модератора. Прокси подтягивается из .env (TELEGRAM_PROXY).
rem Держи это окно открытым, пока модерируешь черновики. Закрыть: Ctrl+C.
cd /d "%~dp0"
"C:\Users\HP\AppData\Roaming\kimi-desktop\daimon-share\daimon\runtime\python\.venv\Scripts\python.exe" main.py moderate
pause
