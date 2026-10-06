@echo off
rem Разовый сбор афиши: парсинг -> фильтр -> генерация постов -> черновики в базе.
rem Черновики потом придут в бот по /pending (модератор должен быть запущен).
rem Лимит черновиков за прогон: AUTOPOSTER_MAX_DRAFTS в .env (по умолчанию 5).
cd /d "%~dp0"
"C:\Users\HP\AppData\Roaming\kimi-desktop\daimon-share\daimon\runtime\python\.venv\Scripts\python.exe" main.py run
pause
