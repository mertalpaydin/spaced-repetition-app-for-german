# The monthly translation job, on Windows

`scripts/monthly_translation_topup.py` spends Azure Translator's free F0
allowance (2,000,000 characters a month, no card) on the carriers that still
have no trusted English gloss. It remembers what the month has already spent, so
it is safe to run more than once.

At today's numbers the corpus needs about **13.4 months** of free tier
(26,933,263 characters left, 2,000,000 a month). Set this up once and leave it.

---

## 1. Make the wrapper

Task Scheduler runs one program, so put the command in a `.cmd` file. Create
`C:\lla\run-monthly-translation.cmd` (change `C:\lla` to wherever the repository
actually is):

```bat
@echo off
cd /d C:\lla
if not exist logs mkdir logs
uv run python -m scripts.monthly_translation_topup >> logs\monthly-translation.log 2>&1
echo EXITCODE %ERRORLEVEL% at %DATE% %TIME% >> logs\monthly-translation.log
exit /b %ERRORLEVEL%
```

Run it once by hand, from a normal Command Prompt, before scheduling anything.
It takes **at least an hour** when there is a full month of budget to spend:
Azure F0 meters 2,000,000 characters per *hour* as well as per month, so the
script paces itself under that and cannot go faster. That is expected, not a
hang.

## 2. Schedule it

Open Command Prompt **as Administrator** and run this one line:

```bat
schtasks /Create /TN "LLA monthly translation" /TR "C:\lla\run-monthly-translation.cmd" /SC MONTHLY /D 2 /ST 03:00 /RL HIGHEST /F
```

- `/SC MONTHLY /D 2` fires on the 2nd of every month. The 2nd, not the 1st, so
  the run is comfortably after Azure's own reset in every timezone.
- `/ST 03:00` is local time.
- `/F` overwrites an existing task of the same name, so re-running this line is
  how you change the schedule.

If the machine is usually asleep at 03:00, open the Task Scheduler GUI (Start,
"Task Scheduler", Task Scheduler Library), right-click **LLA monthly
translation**, Properties, Settings tab, and tick **"Run task as soon as
possible after a scheduled start is missed"**. `schtasks` cannot set that flag
from the command line. A missed run is otherwise simply skipped and the month's
free quota expires unspent.

Weekly instead of monthly is also fine and costs nothing:

```bat
schtasks /Create /TN "LLA monthly translation" /TR "C:\lla\run-monthly-translation.cmd" /SC WEEKLY /D SUN /ST 03:00 /RL HIGHEST /F
```

Three of every four weekly runs will correctly do nothing, exit 0, and say
"Month 2026-09 has already spent its 2,000,000-character budget" in the log.
That is the ledger working.

## 3. Where things go

| What | Path |
|---|---|
| Log (appended, every run) | `C:\lla\logs\monthly-translation.log` |
| Report (overwritten, last run only) | `C:\lla\data\monthly_translation_topup_report.json` |
| Spend ledger (never overwritten, one entry per month) | `C:\lla\data\fixtures\translations\azure_f0_ledger.json` |
| The glosses themselves | `C:\lla\data\fixtures\translations\de_en.jsonl` |

Back up the ledger with the store. They belong together: one is the corpus of
glosses, the other is what it cost.

## 4. How to tell it worked

Open `data\monthly_translation_topup_report.json`. The bottom of it says:

```json
"remaining": {
  "carriers_untrusted": 416118,
  "characters_untrusted": 24886000,
  "months_remaining": 12.44,
  "months_remaining_whole": 13,
  "sentence": "416,118 carriers still lack a trusted machine translation, ..."
}
```

A good run has:

- `this_run.translated` in the tens of thousands (about 33,000 carriers at 59.8
  characters each is a full month's 2,000,000).
- `month_after.remaining_this_month` near 0.
- `remaining.months_remaining` roughly one lower than last month's.
- `warnings` empty.
- `EXITCODE 0` on the last line of the log.

## 5. How to tell it silently did nothing

This is the failure a scheduled job actually has. Three checks, in order:

**Did it run at all?** In Command Prompt:

```bat
schtasks /Query /TN "LLA monthly translation" /V /FO LIST
```

Look at `Last Run Time` and `Last Result`. `Last Result: 0` is success.
`Last Result: 1` is the script telling you it had budget and work and translated
nothing anyway. `Last Run Time: N/A` means it has never fired: check the task is
enabled and that the machine is awake at 03:00.

**Did it translate anything?** Open the ledger,
`data\fixtures\translations\azure_f0_ledger.json`:

```json
{
  "version": 1,
  "months": {
    "2026-09": {
      "azure_characters": 1998430,
      "gemini_characters": 0,
      "batches": 331,
      "runs": 1,
      "last_run_at": "2026-09-02T03:41:12.004000+00:00"
    }
  }
}
```

- **No entry for this month at all** means the job never started. Check the log
  for a Python traceback and check `Last Run Time` above.
- **`runs` above 0 with `azure_characters` at 0** means it started and
  translated nothing. Read `warnings` in the report. The usual causes are a
  missing or expired `AZURE_TRANSLATOR_KEY` in `.env`, or Azure refusing every
  batch.
- **`gemini_characters` above 0** means Azure was failing and the paid Gemini
  fallback picked up the work. That costs real money against the 7.50 USD/month
  ceiling. It stops itself at 100,000 characters a month
  (`--max-gemini-characters-per-month`), but it is a sign something is wrong
  with the Azure resource, not a normal state.

**Is it making progress?** Compare `remaining.carriers_untrusted` in this
month's report with last month's. If it has not fallen by roughly 33,000, the
job is running but not getting through the corpus. `this_run.failed` in the
report and `this_run.failure_examples` say why.

## 6. Turning it off, or changing it

```bat
schtasks /Change /TN "LLA monthly translation" /DISABLE
schtasks /Change /TN "LLA monthly translation" /ENABLE
schtasks /Delete  /TN "LLA monthly translation" /F
```

Deleting the task does not touch the ledger or the store. Re-creating it later
picks up exactly where it stopped.

## 7. Useful flags

Add these inside the `.cmd` file, after `scripts.monthly_translation_topup`:

| Flag | Why |
|---|---|
| `--headroom-characters 20000` | Stop 20,000 characters short of the ceiling, in case Azure's own count disagrees with the ledger's. |
| `--max-characters 100000` | Cap one invocation. Use this for the first supervised run. |
| `--monthly-budget N` | If the Azure resource is not F0. |
| `--checkpoint-every N` | Flush the store every N batches. Lower is safer against a crash, higher is less disk I/O. Default 10. |
| `--seed N` | Changes the order carriers are worked through. Do not change it once the job is running: the order is what makes consecutive months move forward instead of re-drawing the same slice. |

## 8. What this job will and will not do

It replaces every Tatoeba gloss in the store with a machine translation,
because Tatoeba's own translations are not trusted (see `TODO.md`, "Do not
change these without asking the owner": a hand audit of 430 accepted exercises
found 4 wrong glosses and 3 of the 4 were Tatoeba's). It does this **after** it
has glossed every carrier that has no gloss at all, and it never deletes a
Tatoeba record until a real translation has landed to overwrite it, so the
corpus behind `TODO.md` item 11, click a word to see it in context, never has a
hole in it.
