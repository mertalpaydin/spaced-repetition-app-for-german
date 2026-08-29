@echo off
REM One wake-up of the corpus pilot. Driven by the Windows scheduled task
REM "LLA pilot tick", which runs at logon and then every 30 minutes.
REM
REM Each tick collects any batch jobs that have finished since last time, then
REM runs phase B over the candidate pool: cache hits cost nothing, free-lane
REM quota is spent until it is gone, and the remainder is queued as a real
REM Batch API job that a later tick collects.
REM
REM A tick that finds another tick still running exits 0 without doing
REM anything. That is the normal state of a job scheduled more often than it
REM finishes, and a task history full of red is a history nobody reads.
REM
REM PHASE A IS NOT RUN HERE. It is the ~2.5 hours of spaCy over the corpus and
REM has to be run once, by hand, before any of this does anything:
REM
REM   uv run python -m scripts.step7_corpus_pilot --phase a ^
REM       --limit 1000000 --per-topic-quota 25 ^
REM       --pool-file data\corpus_candidate_pool.json
REM
REM Add --write-bank data\bank.db below once the pilot's numbers are trusted
REM and the run is meant to populate the bank rather than only measure.
cd /d "%~dp0"
if not exist logs mkdir logs
uv run python -m scripts.pilot_tick >> logs\pilot-tick.log 2>&1
echo EXITCODE %ERRORLEVEL% at %DATE% %TIME% >> logs\pilot-tick.log
exit /b %ERRORLEVEL%
