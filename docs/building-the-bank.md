# Building the bank

> **This has never been run.** It is a plan with measured figures behind it, not
> a record of a build. There is no item bank.
>
> **The paid route in step 1 will not finish this month.** It costs about
> 3.28 USD and August has spent 5.21 of its 7.50 USD ceiling, so
> `GeminiLlmClient` raises `BudgetExceeded` partway through verification and the
> bank write is refused. September resets the ceiling.
>
> **The route that works today is `--free-lane-only`**, in "Building it without
> spending anything" below, spread across several days. It spends nothing and
> needs no ceiling.
>
> Read `TODO.md` item 2 before starting. It is the same build, with the decision
> that comes first and the two flags that quietly ruin the run.

From a clean checkout to a populated `web/data`. Prerequisites once, then four
commands. Run them in order; each one's "did it work" check is the input the
next one needs.

Costs and durations below are the owner's own measured figures for a
**25-items-per-topic, 49-topic, ~1,225-item** build. Anything marked
"measured in this project's container" is a lower bound on a faster machine.

---

## 0. Prerequisites, once

```bash
uv sync
cp .env.example .env      # then fill in GEMINI_* and AZURE_TRANSLATOR_KEY
```

`uv sync` installs both spaCy models (`de_core_news_sm`, `en_core_web_sm`)
from pinned wheels, so there is no separate `spacy download` step.

Stage the two corpora at `data/raw/_extract/`:

```
data/raw/_extract/tatoeba_deu.tsv
data/raw/_extract/leipzig_sample.txt
```

Neither is in the repository and neither is downloaded for you. Both are
tab-separated plain text, and `scripts/corpus_reading.py` is the one reader for
both:

| File | Source | Format |
|---|---|---|
| `tatoeba_deu.tsv` | Tatoeba's per-language sentence export, `tatoeba.org` | `<id>\t<lang>\t<sentence>` |
| `leipzig_sample.txt` | Leipzig Corpora Collection German news, `wortschatz.uni-leipzig.de` | `<id>\t<sentence>` |

Check each one's terms before publishing anything built from it.
`docs/plan/german-grammar-app-plan.md` section 2, "Sources (all free)", has the
licence notes. Only
lines of 25 to 160 characters and 5 to 18 words are read, so a smaller sample
still works; it just yields fewer items.

**Worked when:** `uv run pytest -q -p no:randomly` passes, and
`ls data/raw/_extract` lists both files. If either corpus is missing the
build still runs and quietly produces fewer items, so check the file list
rather than trusting the exit code.

Cost: none. Duration: a few minutes.

---

## 1. Build the bank

```bash
uv run python -m scripts.step7_corpus_pilot \
    --limit 1000000 \
    --per-topic-quota 25 \
    --verification-passes 2 \
    --verification-batch-size 5 \
    --max-translation-characters 120000 \
    --write-bank data/bank.db
```

What each flag is doing:

| Flag | Why this value |
|---|---|
| `--limit 1000000` | Read the whole corpus, not the 40,000-per-source default. The flag is per source. |
| `--per-topic-quota 25` | The owner's decision: 25 items per topic, 49 topics, about 1,225 items. |
| `--verification-passes 2` | An item is rejected if either pass rejects it. See the correction below on what the evidence for this actually says. |
| `--verification-batch-size 5` | **5, never 20.** Hand-audited: the same 475 candidates gave 444 accepted / 31 rejected at batch 20 and 438 / 37 at batch 5. Diffed item by item, 9 items were accepted at 20 and rejected at 5, and 3 the other way; all 12 were read by hand and **all 12 were genuinely bad**. The smaller batch scrutinises later items in a prompt more closely, and catches three times as many of the disagreements. `DEFAULT_VERIFICATION_BATCH_SIZE` in the code is still 20; always pass this flag. |
| `--max-translation-characters 120000` | **Required. The 60,000 default is too small for this run.** 1,225 carriers at a measured mean of 61.7 characters is about 75,600 characters, so the default guard would stop at a batch boundary and leave several hundred items with no gloss. |
| `--write-bank data/bank.db` | The new flag. Off by default; without it nothing is written to any database. |

**Correction, 2026-08-28.** This table used to justify `--verification-passes 2`
by saying "two passes at batch 5 caught 12 real defects that one pass alone
missed". That misreads the experiment. The 12 defects come from
`scripts/step7_corpus_pilot.py`'s own docstring, and that experiment compared
**batch 20 against batch 5** -- two batch *sizes*, one pass each -- not two
passes at one size. Two passes at the same batch size sample only the model's
own run-to-run noise, which is a smaller effect than the one that was measured.

**Settled by the owner, 2026-08-28: two passes, both at batch 5.** Batch 10 was
considered as a middle ground and rejected: the measured effect is not a dial,
it is a disagreement between two sizes, and 10 would lose batch 5's closer
scrutiny without gaining the diversity. Two passes at one size sample the
model's run-to-run noise rather than the larger between-size effect, so read
`pass_disagreements` in the report rather than assuming the 12-defect figure
transfers.

**Cost:** $3.28 in Gemini verification, measured from the owner's Google
bill at $0.00268 per item. Against a $7.50/month ceiling. The translation is
free: about 75,600 characters against Azure F0's 2,000,000 a month.

**Duration:** hours, most of it silent. Two spaCy passes run over every
corpus line, and neither prints progress; at the rates measured in this
project's container (78 sentences/second for carrier validation, 92 for
tagging) a 450,000-line corpus is roughly 2.5 hours before the first
per-topic table appears. The script prints that estimate up front. The
verification passes add roughly 30 to 45 minutes (490 synchronous calls).

**Worked when:**

```bash
python3 -c "import json; r=json.load(open('data/corpus_pilot_report.json')); \
print(r['bank_write']); print('accepted', r['accepted_total'])"
```

prints `attempted: True`, `error: None`, and `inserted` equal to
`accepted_total`. The exit code is 0 only if every item got a real
verification verdict AND the bank was written.

If the verification backstop could not run for even one item (no API key,
budget exhausted, a transport failure), the run fails and the bank write is
**refused entirely** rather than partially applied: an item nothing judged
must not reach the bank. The review, rejected and report files are still
written, so the failure is diagnosable from disk. Fix the cause and re-run;
nothing was written, so nothing needs undoing.

Re-running this command is safe. Item ids are content hashes, so a second
run reports `inserted: 0` and `skipped_already_present: <n>` rather than
doubling the bank. It also adds nothing: the sampler is deterministic on
`--seed` and measures its per-topic quota against the current run's candidate
pool, not against what is already in `bank.db`. To add more, raise
`--per-topic-quota` (see "Topping up later").

### Running it in two phases

`--phase` splits the run at the point where it stops being deterministic local
work and starts being somebody else's API:

```bash
# Phase A: corpus to candidate pool. No network, no key, no cost.
uv run python -m scripts.step7_corpus_pilot --phase a     --limit 1000000 --per-topic-quota 25     --pool-file data/corpus_candidate_pool.json

# Phase B: translate, verify, write the bank. Re-runnable.
uv run python -m scripts.step7_corpus_pilot --phase b     --pool-file data/corpus_candidate_pool.json     --verification-passes 2 --verification-batch-size 5     --max-translation-characters 120000 --write-bank data/bank.db
```

**Why it matters.** Phase A is the ~2.5 hours of silent spaCy work, and it has
no checkpoint of any kind: the tagger's cache is in-process and dies with the
process, so any interruption used to restart the whole thing from zero. That is
what made a polling scheduled job impossible, because a run stopped after
thirty minutes never reached an LLM call however often it was restarted. With
the pool on disk, phase B is cheap to re-run and can be driven by a scheduler.

`--phase both` is the default and is exactly the behaviour this script had
before the split. Verified on an 800-line slice: the two routes produce
identical reports, item for item and count for count.

**The pool records a fingerprint** of the phase-A inputs that decide what is in
it (corpus paths, per-source limit, quota, seed, lemma cap) and phase B
**warns** on a mismatch rather than refusing, because re-verifying an older pool
with different verification settings is a legitimate experiment. The
verification flags are deliberately not in the fingerprint.

## The scheduled pilot tick

`scripts/pilot_tick.py` is one wake-up. It collects any batch jobs that
finished since last time, then runs phase B over the candidate pool: cached
responses cost nothing, free-lane quota is spent until it is gone, and the
remainder queues as a real Batch API job for a later tick to collect.

```bat
schtasks /Create /TN "LLA pilot tick" /TR "C:\Users\merta\Desktop\Language_Learning_App\run-pilot-tick.cmd" /SC MINUTE /MO 30 /F
```

Then, in PowerShell, the settings `schtasks` cannot set:

```powershell
$s = Get-ScheduledTask -TaskName "LLA pilot tick"
$s.Settings.StartWhenAvailable = $true
$s.Settings.ExecutionTimeLimit = "PT4H"
$s.Settings.MultipleInstances = "IgnoreNew"
$s.Settings.DisallowStartIfOnBatteries = $false
$s.Settings.StopIfGoingOnBatteries = $false
Set-ScheduledTask -TaskName "LLA pilot tick" -Settings $s.Settings
```

**The task ships DISABLED, and is disabled again as of 2026-09-08.** Two
things to know before enabling it, both learnt that day:

- It runs under an interactive logon, so **a console window appears on the
  desktop** for as long as a tick runs. That is the tick, not a hang.
- Its phase B command has no `--require-gloss`, so a tick **translates** every
  un-glossed pool carrier on Azure before verifying, against the shared Azure
  ledger (phase B now reads and writes it). Do not enable it while another
  phase B is running, and read `_gloss_translator` in
  `scripts/step7_corpus_pilot.py` for what decides whether a run translates.

Enable it only once phase A has produced a pool:

```powershell
schtasks /Change /TN "LLA pilot tick" /ENABLE
```

Until then every tick would exit 1 saying there is no pool, forty-eight times a
day, and a task history that is all red is a history nobody reads.

### Phase A is not part of a tick

Phase A is the ~2.5 hours of spaCy over the corpus. Run it once, by hand:

```bash
uv run python -m scripts.step7_corpus_pilot --phase a \
    --limit 1000000 --per-topic-quota 25 \
    --pool-file data/corpus_candidate_pool.json
```

Putting it inside a thirty-minute tick would mean every tick restarted it and
no tick ever finished it, which is the exact failure the phase split removed.

### Two locks, not one

- **`MultipleInstances = IgnoreNew`** tells Task Scheduler not to start a
  second copy.
- **`.cache/pilot_tick.lock`** (`src/run_lock.py`) enforces the same thing for
  any run started another way, by hand or by a second machine sharing the
  directory.

Both exist because the cost of getting it wrong is not a wasted run: two
concurrent phase-B runs would both find the same prompts uncached and both
submit them as batch jobs, paying twice for work that is used once. A tick that
finds the lock held exits **0**, because "the previous one is still going" is
the normal state of a job scheduled more often than it finishes.

The lock recovers from a crashed holder two ways: the recorded process being
gone (checked without `os.kill`, which on Windows would terminate the process
it is asking about), and an age backstop of six hours for a PID that has been
recycled.

### Watching it

```powershell
Get-Content -Wait -Tail 40 logs\pilot-tick.log
schtasks /Query /TN "LLA pilot tick" /FO LIST /V | Select-String "Status|Last Result"
uv run python -m scripts.collect_batch_jobs --status
```

`--status` is the one that answers "is anything outstanding at Google", and it
contacts nobody.

### What a tick costs

Once enabled and once a pool exists, a tick spends free-lane quota first and
then submits paid batch work. That is real money against the 7.50 USD/month
ceiling, which `GeminiLlmClient` still enforces: a tick that would breach it
raises `BudgetExceeded`, the items degrade to `not_run`, and the bank write is
refused. Nothing silently overspends, but nothing asks first either.

### Building it without spending anything

`--free-lane-only` builds the client with `forbid_paid_lane=True`, so the run
uses the unbilled Gemini project or stops:

```bash
uv run python -m scripts.step7_corpus_pilot \
    --free-lane-only \
    --limit 1000000 \
    --per-topic-quota 25 \
    --verification-passes 1 \
    --verification-batch-size 5 \
    --max-translation-characters 120000 \
    --write-bank data/bank.db
```

It refuses to start unless `GEMINI_FREE_API_KEY` is set, and it will not fall
back to `GEMINI_API_KEY`, which may belong to the billed project. Put the
unbilled project's key in `.env`:

```
GEMINI_FREE_API_KEY=<the key from the UNBILLED Google Cloud project>
```

Two things to expect, both consequences of the free lane's daily request
limit rather than of this flag:

- **The run will probably need several days.** When the daily quota runs out,
  `GeminiLlmClient` raises `PaidLaneForbiddenError`, every item reports
  `not_run`, the bank write is refused and the exit code is 1. Re-run the same
  command the next day: pass 1 replays every verdict already in
  `.cache/llm` for free and spends the new day's quota only on what is left,
  so each run gets further than the last until one finishes and writes the
  bank.
- **Use `--verification-passes 1` for this.** Pass 2 runs with the cache
  bypassed on purpose (a cached second pass would replay pass 1 and measure
  nothing), so it makes no progress across days: it has to complete its whole
  245 requests inside one day's quota or not at all. With one pass, the day
  the cache is complete costs zero requests and the bank is written. Two-pass
  verification is the better instrument and stays the recommended build when
  paid spend is available.

The translation side needs nothing: Azure F0 has no paid fallback path in
this codebase, and this build spends about 75,600 of its 2,000,000 monthly
characters.

---

## 2. Check the glosses actually landed

```bash
python3 -c "import sqlite3; c=sqlite3.connect('data/bank.db'); \
print('items', c.execute('SELECT COUNT(*) FROM items').fetchone()[0]); \
print('with gloss', c.execute('SELECT COUNT(*) FROM items WHERE gloss_en IS NOT NULL').fetchone()[0]); \
print('schema', c.execute('SELECT MAX(version) FROM schema_version').fetchone()[0])"
```

**Worked when:** `schema` is `4`, `items` is about 1,225, and `with gloss` is
close to `items`. A large gap means the translation budget ran out or no
translator was configured; the report's `gloss` block says which
(`skipped_for_budget`, `translator_mode`, `gloss_missing_stale_tatoeba`).

Cost: none. Duration: instant.

---

## 3. Export to the web bundle

```bash
uv run python -m scripts.step3_export_web_data
```

**Worked when:** it prints `Export Succeeded!` with a `Total Items Exported`
matching the item count from step 2, and:

```bash
python3 -c "import json; m=json.load(open('web/data/manifest.json')); \
print(m['total_items'], m['topic_count']); \
a=json.load(open('web/data/all_items.json')); \
print('with gloss', sum(1 for i in a if i.get('gloss_en')))"
```

reports the same totals, with `with gloss` matching step 2. `gloss_en` has
been dropped twice on this path before (once for want of a database column,
once for want of an export allowlist entry), so check it here rather than
assuming it.

Cost: none. Duration: seconds.

---

## 4. Run the app

```bash
uv run python -m scripts.step4_run_app
```

**Worked when:** an exercise appears with its English translation under it.

---

## Topping up later

Nightly top-up is the last resort, not the build path. To add more items from
the same corpora, re-run step 1 with a higher `--per-topic-quota` and the same
`--seed`: the extra items are new ids and insert; everything already banked is
skipped. Then re-run steps 2 and 3.

One known trap: `gloss_en` is not part of the item id, so an item banked
without a gloss keeps its NULL even if a later run has a gloss for it. A
re-run skips that id as a duplicate and does not refresh it. The run reports
the count as `bank_write.stale_gloss_rows` and prints a warning. There is no
automated backfill; either leave them (the client renders no translation for
a NULL gloss, which is what it is built to do) or delete those rows and let
the next run re-insert them:

```bash
python3 -c "import sqlite3; c=sqlite3.connect('data/bank.db'); \
n=c.execute(\"DELETE FROM items WHERE gloss_en IS NULL AND id LIKE 'corpus_%'\").rowcount; \
c.commit(); print('deleted', n)"
```

Only do that on a bank with no `review_logs` rows against those items.
