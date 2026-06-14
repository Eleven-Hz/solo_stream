@echo off
rem 개발용 실행: 사용자 데이터를 %APPDATA% 대신 프로젝트 폴더에 둔다 (지금과 동일 경로).
rem 배포 exe 동작(%APPDATA%)을 테스트하려면 이 줄을 주석 처리하고 빌드본을 실행.
set "SOLO_STREAM_DEV=1"
rem Solo Stream launcher (no console window).
rem Launches the console-less pythonw.exe in hidden mode.
rem Logs are written to the logs\ folder, so no console is needed.
rem For debugging (logs in console), use run_debug.bat instead.

set "VBS=%temp%\solo_stream_launch.vbs"
> "%VBS%" echo Set sh = CreateObject("WScript.Shell")
>>"%VBS%" echo sh.CurrentDirectory = "%~dp0"
>>"%VBS%" echo sh.Run "venv\Scripts\pythonw.exe -m src.main", 0, False
wscript "%VBS%"
del "%VBS%" >nul 2>&1
