@echo off
rem Run IG Studio from source. Double-click it, or drag a trip folder onto this file.
rem (No Python needed at all? Use dist\IGStudio.exe instead - see README "Build the portable exe".)
rem Python is found in this order (see README "Install from source"):
rem   1. the IGSTUDIO_PYTHON environment variable
rem   2. the "igstudio" conda environment
setlocal
title IG Studio
set "PY=%IGSTUDIO_PYTHON%"
if not defined PY if exist "%USERPROFILE%\miniconda3\envs\igstudio\python.exe" set "PY=%USERPROFILE%\miniconda3\envs\igstudio\python.exe"
if not defined PY if exist "%USERPROFILE%\anaconda3\envs\igstudio\python.exe" set "PY=%USERPROFILE%\anaconda3\envs\igstudio\python.exe"
if not defined PY (
    echo Could not find the "igstudio" conda environment.
    echo Create it with:  conda env create -f environment.yml
    pause
    exit /b 1
)
"%PY%" "%~dp0ig_studio.py" %*
if errorlevel 1 pause
