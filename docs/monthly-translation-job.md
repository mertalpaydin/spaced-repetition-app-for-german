# The monthly translation job, on Windows

> **Set up and running as of 2026-08-28.** The task exists, it is scheduled
> **daily**, and it has spent its first characters. What follows is how it was
> built and how to check on it, not work still to do. Section 2 shows the
> monthly form this was originally written for; section 2a is what is actually
> in use.

`scripts/monthly_translation_topup.py` spends Azure Translator's free F0
allowance (2,000,000 characters a month, no card) on the carriers that still
have no trusted English gloss. It remembers what the month has already spent, so
it is safe to run more than once.

> **Changed 2026-09-08: the month ends when Azure says so, not when the count
> says so.** The ledger still counts characters, but the count is an estimate:
> it only sees what this job sent, and on 2026-09-08 a pilot run put ~66,000
> characters through Azure that no ledger saw, while the ledger read "679
> left" and Azure kept answering. The gate is now
> `azure_quota_rejected` in the ledger: a run keeps sending batches until Azure
> answers a quota 403, records that, and stops; every later run that month
> sends nothing. `--headroom-characters` no longer stops anything. Owner's
> instruction, that day.

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

## 2a. What is actually scheduled (daily, and why)

The task in use is **daily**, not monthly:

```bat
schtasks /Create /TN "LLA monthly translation" /TR "C:\Users\merta\Desktop\Language_Learning_App\run-monthly-translation.cmd" /SC DAILY /ST 03:00 /F
```

**Daily buys no extra quota.** The F0 allowance is 2,000,000 characters per
calendar month, keyed UTC, and running more often cannot raise it. What daily
buys is that **a missed month becomes impossible**. A monthly trigger fires
once; a machine switched off at that moment costs the entire month's allowance,
and it cannot be recovered. With a daily trigger, the first day the machine is
on spends the month's budget, and every later run that month correctly does
nothing and exits 0. The ledger is what makes those extra runs safe.

Three settings `schtasks` cannot set were applied afterwards with PowerShell,
and they matter on a laptop:

```powershell
$s = Get-ScheduledTask -TaskName "LLA monthly translation"
$s.Settings.StartWhenAvailable = $true          # catch up after a missed 03:00
$s.Settings.ExecutionTimeLimit = "PT6H"
$s.Settings.DisallowStartIfOnBatteries = $false # run on battery
$s.Settings.StopIfGoingOnBatteries = $false     # do not stop when unplugged
Set-ScheduledTask -TaskName "LLA monthly translation" -Settings $s.Settings
```

`StartWhenAvailable` is the important one. Without it, a daily task on a machine
that is never on at 03:00 simply never runs, which is the same failure the daily
schedule was meant to prevent.

**Do not read the ledger or the store while a run is in progress.** On Windows
`os.replace` fails with `WinError 5` or `32` if any other process holds the
destination open, even for reading. That killed the first real run at 27% of the
month. `save_ledger_atomic` now retries for about three seconds, which covers a
reader or an antivirus scan, but the habit to keep is to watch the process
rather than its output:

```powershell
schtasks /Query /TN "LLA monthly translation" /FO LIST /V | Select-String Status
```

**Watch a run through the ledger, not the log.** `logs\monthly-translation.log`
stays empty until the process exits, because Python buffers its output when it
is redirected to a file. The ledger updates every checkpoint:

```powershell
Get-Content data\fixtures\translations\azure_f0_ledger.json
```

---

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
"Azure refused month 2026-09 on quota at ..." in the log. That is the ledger
working.

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
      "last_run_at": "2026-09-02T03:41:12.004000+00:00",
      "azure_quota_rejected": true,
      "azure_quota_rejected_at": "2026-09-02T03:41:12.004000+00:00"
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
- **`azure_quota_rejected` false at the end of a full run** means Azure never
  said no, so the month is not finished from Azure's point of view whatever
  `azure_characters` reads. The next daily run keeps sending. This is the
  normal state on every day before the allowance is actually spent.
- **`azure_quota_rejected` true** means Azure refused a batch. Nothing more
  is sent this month, and nothing needs doing. `scripts/step7_corpus_pilot.py`
  reads the same flag and translates nothing either.
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
| `--headroom-characters 20000` | Since 2026-09-08 this changes only the printed allowance and the months-remaining estimate. It does not stop a run; Azure's refusal does. |
| `--max-characters 100000` | Cap one invocation. Use this for the first supervised run. The only character figure that still stops a run. |
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
