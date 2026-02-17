@echo off
REM Build script for VoiceClient
REM This script builds a single-file executable with PyInstaller

echo ========================================
echo Building VoiceClient with PyInstaller
echo ========================================
echo.

REM Activate virtual environment if it exists
if exist venv\Scripts\activate.bat (
    echo Activating virtual environment...
    call venv\Scripts\activate.bat
    echo.
) else if exist .venv\Scripts\activate.bat (
    echo Activating virtual environment...
    call .venv\Scripts\activate.bat
    echo.
) else (
    echo No virtual environment found, using global Python
    echo.
)

REM Clean previous builds
echo Cleaning previous builds...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist VoiceClient.exe del /q VoiceClient.exe
echo.

REM Build with PyInstaller
echo Building executable...
python -m PyInstaller --clean VoiceClient_improved.spec

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ========================================
    echo BUILD FAILED!
    echo ========================================
    pause
    exit /b 1
)

REM Move executable to root
if exist dist\VoiceClient.exe (
    echo.
    echo Moving executable to root directory...
    echo.
    echo ========================================
    echo BUILD SUCCESSFUL!
    echo ========================================
    echo.
    echo Executable: VoiceClient.exe
    echo.
    echo To avoid antivirus false positives:
    echo 1. Add VoiceClient.exe to your antivirus exclusions
    echo 2. Sign the executable with a code signing certificate
    echo 3. Submit to antivirus vendors for whitelisting
    echo.
) else (
    echo.
    echo ========================================
    echo BUILD FAILED - Executable not found!
    echo ========================================
)

pause
