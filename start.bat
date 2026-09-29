@echo off
rem ============================================================
rem  Double-click launcher only - the real logic lives in start.py
rem
rem  This file is intentionally tiny.  Every batch construct the
rem  old launcher kept here (labels, delayed expansion, nested
rem  quotes, a for-in option whitelist, non-ASCII text) was a way
rem  for the entry point to break silently, and cmd.exe is blocked
rem  by this machine's security policy so it cannot be test-run.
rem
rem  Why it still exists: this machine associates .py with an IDE,
rem  so double-clicking start.py opens the editor instead of
rem  running it.  That is the ONLY job of this file.
rem
rem  Usage:  start.bat [option] [extra args ...]
rem  equals  python start.py [option] [extra args ...]
rem  Option list:  python start.py help     (start.py is the
rem  single source of truth for every option and every behaviour)
rem ============================================================
setlocal
set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" set "PY=py"
"%PY%" "%~dp0start.py" %*
