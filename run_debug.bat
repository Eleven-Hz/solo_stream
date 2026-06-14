@echo off
rem 개발용 실행(콘솔 로그): 사용자 데이터를 프로젝트 폴더에 둔다 (지금과 동일 경로).
set "SOLO_STREAM_DEV=1"
call venv\Scripts\activate.bat
python -m src.main
pause
