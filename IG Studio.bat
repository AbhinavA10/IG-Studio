@echo off
title IG Studio
set "PY=%USERPROFILE%\miniconda3\envs\igstudio\python.exe"
if not exist "%PY%" (
  echo Could not find the igstudio conda environment at %PY%
  pause
  exit /b 1
)
"%PY%" "%~dp0ig_studio.py"
pause
