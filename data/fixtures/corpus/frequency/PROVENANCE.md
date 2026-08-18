# Provenance: frequency data for the B2-and-above vocabulary band

This directory holds the two inputs `src/lexicon/frequency.py`'s
`FrequencyBander` uses to derive the B2 CEFR band (see
`scripts/step1_extract_vocab.py`), replacing the previous B2 source, which
was a scrape of an unofficial course glossary and is documented as
unreliable below.

## `de_opensubtitles2018_top50k.txt` -- the frequency ranking

- **Source**: [hermitdave/FrequencyWords](https://github.com/hermitdave/FrequencyWords),
  file `content/2018/de/de_50k.txt`.
- **Underlying corpus**: OpenSubtitles 2018 (`http://opus.nlpl.eu/OpenSubtitles2018.php`),
  German subtitle text, tokenized and frequency-counted by that repository's
  generator script.
- **Fetched**: 2026-08-18, from `raw.githubusercontent.com` (verbatim copy,
  unmodified).
- **Licence**: MIT for the repository's code; **CC BY-SA 4.0** for the
  generated frequency-list content itself, per that repository's `README.md`
  ("License" section). CC BY-SA 4.0 permits redistribution and derivative
  works with attribution and share-alike; this file is redistributed
  verbatim (attribution: hermitdave/FrequencyWords, OpenSubtitles 2018), and
  any derivative data file built from it (the frequency-derived slice of
  `vocab_levels.json`) should be treated as share-alike under the same
  licence.
- **Entries**: 50,000 lines, `{word} {count}`, most frequent first.
- **Format**: raw tokenized surface forms (not lemmas) from subtitle text,
  lower- and mixed-case, including a substantial amount of interjections,
  slang, profanity, and non-German proper names (subtitle dialogue is
  informal and multilingual at the margins -- see "known limitations"
  below).

## `de_dictionary_filter.txt` -- the real-word filter

- **Source**: [enz/german-wordlist](https://github.com/enz/german-wordlist),
  file `words` (a wordgame/Scrabble-legality wordlist for German, used by
  the Tanglet word game). Per that repository's `README.md`, the list
  explicitly **excludes names, proper nouns, toponyms, and abbreviations**,
  which is exactly the contamination the frequency list above needs
  filtered out.
- **Fetched**: 2026-08-18, from `raw.githubusercontent.com`.
- **Licence**: **CC0 1.0 Universal** (public domain dedication), per that
  repository's `COPYING` file. No attribution is legally required; this
  file is credited anyway for traceability.
- **Entries**: the upstream list has 685,789 entries. This file is a
  **derived subset**: only entries that also appear (case/umlaut/eszett
  normalised, via `src.lexicon.lemmatizer.normalise`) in
  `de_opensubtitles2018_top50k.txt` are kept, since that is the only
  membership test this pipeline ever performs. That is 37,567 entries. This
  keeps the vendored file two orders of magnitude smaller than the upstream
  685k-word list while remaining exactly reproducible from the two public
  sources above (regenerate by re-fetching both upstream files and
  intersecting, normalised).

## How the two are combined into `vocab_levels.json`'s B2 band

`FrequencyBander.derive_b2_band` (see `src/lexicon/frequency.py`):

1. Walk `de_opensubtitles2018_top50k.txt` in rank order (most frequent
   first).
2. Keep only alphabetic tokens, length >= 3, that also appear in
   `de_dictionary_filter.txt` (the real-word filter -- drops the English
   names, slang tokens and OCR artefacts otherwise present in the raw
   subtitle frequency list; see "known limitations").
3. Drop any token that already resolves (directly, or via
   `src.lexicon.lemmatizer.lemma_candidates`) to an existing A1, A2 or B1
   entry in `vocab_levels.json` -- those bands stay list-derived from the
   official Goethe wordlists, per `docs/audits/stage-04-recovery-plan.md`
   fix C and the task that produced this file.
4. Take the next `FrequencyBander.DEFAULT_B2_BAND_SIZE` (6,000) surviving
   tokens, in rank order, as the frequency-derived B2 band.

A lemma beyond that band, or absent from the top-50k frequency corpus
entirely, is not labelled B2 by this pipeline. It is not labelled
above-B2 either, because `CEFR` (`src/contracts.py`) has no level above B2
to assign it to (see the report accompanying this change for why that
contract was not touched): it stays absent from `vocab_levels.json`, which
`VocabularyStore` treats as "unknown", not "too hard" (see
`docs/audits/stage-04-recovery-plan.md` fix C and
`VocabularyStore.is_within_ceiling`'s docstring). What frequency evidence
*does* buy here is that a word actually earns its B2 label instead of
silently falling into "unknown -- passes"; the count of words landing in
"unknown" versus rank-6000 is exactly the boundary this file makes legible
and reproducible instead of implicit.

## Known limitations (be honest, not authoritative)

- OpenSubtitles-derived frequency is noisy even after dictionary filtering:
  informal register (`scheiße`, `verdammt`, `arsch`) ranks highly because
  film dialogue is informal, not because those words are B2-appropriate
  exam vocabulary. The dictionary filter removes non-words but not
  register.
- The frequency corpus underrepresents written/formal/bureaucratic
  vocabulary relative to spoken dialogue -- compound nouns like
  "Projektleiter" do not appear in the top 50k at all despite being
  ordinary B2 vocabulary, because subtitle dialogue rarely says the word
  "Projektleiter". `VocabularyStore` compensates for this specific gap with
  a conservative compound-decomposition fallback (see
  `src/lexicon/lemmatizer.py`, `compound_split_candidates`), not with more
  frequency data.
- This is one public frequency source, not a cross-checked or
  vendor-diverse one (contrast CLAUDE.md section 10's two-vendor protocol
  for agent-critical artefacts). It is offered as a genuine improvement
  over an 808-entry, English-contaminated scrape (see the report), not as
  a definitively "correct" B2 boundary.
