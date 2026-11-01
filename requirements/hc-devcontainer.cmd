@echo off
rem Windows runs neither shebangs nor files without an extension: run the helper
rem beside this file with Python 3 (the py launcher, else python).
setlocal
set "python=python"
where py >nul 2>&1 && set "python=py -3"
%python% "%~dp0hc-devcontainer" %*
exit /b %errorlevel%
