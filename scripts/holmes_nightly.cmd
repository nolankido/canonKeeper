@echo off
rem Nightly Holmes run: rebuild the bible, audit it, check the Book 7 draft, run the benchmark.
rem Each step logs to logs\*3.log; a step stopped by quota exits non-zero and the chain stops
rem there (everything is cached, so the next night resumes with the same script).
rem Launch detached:  powershell -NoProfile -File scripts\detach.ps1 scripts\holmes_nightly.cmd
cd /d "%~dp0.."
if not exist logs mkdir logs
echo started %date% %time% > logs\nightly.status
rem These runs feed the README's numbers: one model per stage, or stop (and resume next night)
rem rather than quietly finish on a fallback model, as half of one benchmark once did.
set CANONKEEPER_MODEL_FALLBACKS_ENABLED=false
.venv\Scripts\canonkeeper quota > logs\quota_start.log 2>&1
.venv\Scripts\canonkeeper build evals\corpora\sherlock-holmes\series.json > logs\build3.log 2>&1 || goto :fail
echo build ok >> logs\nightly.status
.venv\Scripts\canonkeeper audit evals\corpora\sherlock-holmes\series.json > logs\audit3.log 2>&1 || goto :fail
echo audit ok >> logs\nightly.status
.venv\Scripts\canonkeeper check evals\corpora\sherlock-holmes\series.json evals\demo\holmes-book-07-draft.md > logs\check3.log 2>&1 || goto :fail
echo check ok >> logs\nightly.status
.venv\Scripts\canonkeeper eval evals\corpora\sherlock-holmes\series.json --n 20 --seed 0 > logs\eval3.log 2>&1 || goto :fail
echo eval ok >> logs\nightly.status
echo done %date% %time% >> logs\nightly.status
exit /b 0
:fail
echo failed with %errorlevel% at %date% %time% >> logs\nightly.status
exit /b 1
