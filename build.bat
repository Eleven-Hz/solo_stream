@echo off
rem Solo Stream 포터블(onedir) 빌드.
rem 사전: venv 에 requirements.txt + requirements-build.txt 설치 완료.
rem 산출: dist\SoloStream\SoloStream.exe + _internal\
setlocal

echo [build] clean build/ dist/
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [build] PyInstaller 실행
call venv\Scripts\pyinstaller.exe solo_stream.spec
if errorlevel 1 (
    echo [build] 실패: PyInstaller 종료 코드 %errorlevel%
    exit /b %errorlevel%
)

echo [build] 완료: dist\SoloStream\SoloStream.exe
endlocal
