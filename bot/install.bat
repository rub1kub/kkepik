@echo off
chcp 65001 >nul
echo ============================================================
echo  Установка зависимостей -- KKEP Bot
echo ============================================================
echo.

:: Проверяем Python (стандартный PATH)
python --version >nul 2>&1
if %errorlevel% equ 0 (
    set PYTHON=python
    goto :found
)

:: Проверяем py-лаунчер
py --version >nul 2>&1
if %errorlevel% equ 0 (
    set PYTHON=py
    goto :found
)

:: Явный путь после winget/стандартной установки
set PYPATH=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if exist "%PYPATH%" (
    set PYTHON=%PYPATH%
    goto :found
)

echo [ОШИБКА] Python не найден.
echo.
echo Установите Python 3.10+ одним из способов:
echo   1. winget install Python.Python.3.12
echo   2. https://python.org/downloads
echo   3. D:\python-3.12.10-amd64.exe
echo.
pause
exit /b 1

:found
echo [OK] Python найден:
%PYTHON% --version
echo.

:: Переходим в директорию бота
cd /d "%~dp0"

:: Обновляем pip
echo Обновляем pip...
%PYTHON% -m pip install --upgrade pip --quiet
echo.

:: Устанавливаем зависимости
echo Устанавливаем зависимости из requirements.txt...
echo.
%PYTHON% -m pip install -r requirements.txt

if %errorlevel% neq 0 (
    echo.
    echo [ОШИБКА] Не удалось установить зависимости.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  [OK] Все зависимости установлены!
echo ============================================================
echo.

:: Проверяем ключевые модули
echo Проверяем импорты...
%PYTHON% -c "import aiogram, fastapi, uvicorn, pandas, pdfplumber, openpyxl; print('[OK] bot: aiogram fastapi uvicorn pandas pdfplumber openpyxl')"
echo.
pause
