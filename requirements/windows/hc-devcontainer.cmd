@echo off
rem The app runs this through cmd: Windows starts neither shebangs nor files
rem without an extension. Run hc-devcontainer.py beside it with Python 3.
setlocal
set "python=python"
where py >nul 2>&1 && set "python=py -3"
%python% "%~dp0hc-devcontainer.py" %*
exit /b %errorlevel%
