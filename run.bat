@echo off
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
