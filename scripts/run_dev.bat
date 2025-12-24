@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."

if not exist ".venv\Scripts\python.exe" (
  py -3.10 -m venv .venv
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
pip install -r requirements.txt

python run.py
endlocal
