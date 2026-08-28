@echo off
REM Standing Azure F0 translation top-up. Driven by the Windows scheduled task
REM "LLA monthly translation", which runs DAILY.
REM
REM Daily, not monthly, and not for extra quota: the F0 allowance is 2,000,000
REM characters per CALENDAR MONTH, so running more often buys none. It runs
REM daily so that a missed month becomes impossible. A monthly trigger fires
REM once, and a machine that is switched off at that moment costs the whole
REM month's allowance, which cannot be recovered. The ledger at
REM data\fixtures\translations\azure_f0_ledger.json is what makes the extra
REM runs safe: every run after the month's budget is spent does nothing and
REM exits 0.
REM
REM A run that has budget takes at least an hour. Azure F0 meters 2,000,000
REM characters per HOUR as well as per month, so the script paces itself
REM underneath that. That is not a hang.
cd /d "%~dp0"
if not exist logs mkdir logs
uv run python -m scripts.monthly_translation_topup >> logs\monthly-translation.log 2>&1
echo EXITCODE %ERRORLEVEL% at %DATE% %TIME% >> logs\monthly-translation.log
exit /b %ERRORLEVEL%
