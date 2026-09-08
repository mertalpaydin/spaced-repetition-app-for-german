# 01: Foundation (Stages 0 to 2)

> **STALE as of 2026-09-08.** This describes the German grammar trainer, which was shut down and pruned on `feat/phrase-deck`. Kept for the reasoning only. The current project is the phrase trainer; read `docs/project-state.md`.
>
> **Design record, written before most of the code existed. Not a description
> of what the code does now.**
>
> The corpus pipeline that makes every exercise today, and the English
> translation shown under every one, are absent from these contracts entirely:
> neither "blank" nor "Azure" appears in this document. `docs/project-state.md`
> has both.
>
> Named departures: it calls the paid lane "batch only", and `CLAUDE.md`'s
> "Two lanes, two projects" section supersedes that; it names
> `data/taxonomy/topics.yaml`, and the file is `data/taxonomy.yaml`. Its
> Definition-of-Done boxes are unticked because nobody ticked them, not because
> the work is undone.

---

## Stage 0: Repo scaffold and CI

**Branch:** `stage/00-scaffold`

### Goal

A repository where a failing test blocks a merge, before any real code exists.

### Deliverables

- `pyproject.toml`: Python 3.12, ruff, mypy, pytest, pytest-cov, hypothesis, pydantic v2
- `src/contracts.py` with the core enumerations from `00-index.md`
- `src/llm/client.py` skeleton: the single LLM entry point, with cost logging and `BudgetExceeded`, initially raising `NotImplementedError` for the transport
- `.env.example`, `.gitignore`, `README.md`
- `.github/workflows/ci.yml`: lint, type check, unit tests, coverage gate
- Branch protection on `main`: require CI pass, require PR

### Tests

```python
def test_contracts_enums_are_frozen_and_complete()
    # every literal set matches the documented values exactly

def test_llm_client_is_the_only_module_importing_the_sdk()
    # AST-scan src/ for the provider SDK import; assert it appears
    # only in src/llm/client.py

def test_budget_exceeded_raised_when_month_to_date_over_ceiling()
    # fake cost_log at ceiling; assert call raises before transport is touched

def test_cost_log_row_written_for_every_call()
    # mock transport; assert exactly one row per call, with token counts

# --- Two-lane execution ---

def test_free_lane_never_uses_batch_and_paid_lane_always_does()

def test_rpm_429_backs_off_and_stays_on_free_lane()
    # mock a 429 carrying RPM quota metadata; assert retry on the same lane

def test_rpd_429_closes_free_lane_until_pacific_midnight()
    # mock a 429 carrying daily-quota metadata; assert the lane is marked
    # closed and no further free calls are attempted until the reset.
    # Clock is injected; the test must not depend on real time.

def test_overflow_accumulates_into_one_batch_not_per_request_failover()
    # 400 queued items with free quota for 250: assert exactly one
    # batch submission containing 150, not 150 separate paid calls.
    # Per-request failover forfeits the batch discount on the
    # requests you actually pay for.

def test_user_content_uses_free_lane_when_restriction_disabled()
    # privacy.restrict_user_content_to_paid_lane = false (the default):
    # grading, explanation and analysis calls may use the free lane

def test_user_content_rejected_from_free_lane_when_restriction_enabled()
    # flag = true: the same three purposes are routed to the paid lane

def test_restriction_flag_is_read_from_config_not_hardcoded()

def test_restriction_flag_value_recorded_at_run_start()
    # so a past run's privacy mode is recoverable from the log

def test_lane_recorded_in_every_cost_log_row()

# --- Local cache ---

def test_cache_checked_before_transport_on_both_lanes()

def test_cache_hit_writes_zero_cost_row_with_lane_cache()
    # hit rate must be measurable from the cost log

def test_cache_key_is_hash_of_full_request()
    # a changed spec sheet must miss; an identical request must hit

def test_rerun_after_crash_regenerates_nothing()
    # the cache is the idempotency mechanism

def test_no_provider_side_context_cache_is_configured()
    # deliberate: it discounts input, which is ~15% of spend,
    # and adds an hourly storage charge that can exceed the saving

def test_ci_workflow_runs_lint_types_and_tests()
    # parse ci.yml; assert all three steps present and none use continue-on-error
```

The SDK-import scan test is the enforcement mechanism for rule 4 in `CLAUDE.md`. It is not decorative. Write it now, before there is anything to violate it.

### Two-lane setup

Two Google Cloud projects are required, not optional: quota is per project rather than per key, and enabling billing on a project removes its free tier entirely.

```
GEMINI_FREE_API_KEY      # unbilled project, synchronous, Flash-Lite
GEMINI_PAID_API_KEY      # billed project, batch only
```

```yaml
# config.yaml
privacy:
  restrict_user_content_to_paid_lane: false   # default: free lane for everything
```

Single-user personal tool: leave it `false`. Set it `true` if the app ever serves anyone but you, since the three user-text calls (production grading, explanations, weekly analysis) would otherwise send another person's writing into free-tier product improvement.

Record the live RPM / RPD / TPM from the AI Studio rate-limit view for each project into `docs/audits/stage-00-quota.md`, **together with current per-million prices for every model in the routing table**. Published tables go stale: free quotas were cut substantially in December 2025, 3.6 Flash launched in July 2026 below 3.5 Flash on output, and 2.5 Flash-Lite retires in October 2026.

If a routing slot is now better filled by a different model, say so and recommend the swap rather than following the table. Check two things specifically: whether a newer Lite tier costs *more* than the one it replaced, which has happened, and whether the model occupying a free-tier slot is still free-tier eligible.

### Definition of Done

- [ ] CI runs and fails the build on a deliberately broken test
- [ ] `main` cannot be pushed to directly
- [ ] The SDK-import scan test passes and would fail if a second module imported the SDK
- [ ] Both lanes configured, with real observed quota AND current pricing recorded in the audit file
- [ ] Routing table either confirmed current or a swap recommended with figures
- [ ] Local cache in place and its hit rate visible in `cost_log`

---

## Stage 1: Taxonomy

**Branch:** `stage/01-taxonomy`

### Goal

`data/taxonomy/topics.yaml`: 75 to 90 German grammar topics, A1 to B2, with a validated prerequisite DAG and confusion groups. This is the spine of the system.

### Inputs

Goethe-Institut Prüfungsziele PDFs for A1, A2, B1, B2. Extract the Grammatik inventory from each. Cross-check ordering against the BAMF Rahmencurriculum.

**[AGENT-CRITICAL]** This is the one artefact everything else is built on, and an error here propagates silently through every generated item and every progress row. External anchor: the Goethe inventories, which fix the topic list independently of any model. The prerequisite edges and confusion groups have no external key and rest entirely on the two-agent protocol, so their second-agent diff is committed alongside them.

### Contract

```python
class Topic(BaseModel):
    id: str                       # snake_case, stable forever
    name_de: str
    cefr: CEFR
    prereqs: list[str]
    confusion_group: str | None
    description: str              # what the topic covers, for the spec sheet
    morph_spec: dict[str, str]    # e.g. {"Case": "Dat", "Number": "Sing"}
                                  # used by the morphology verifier in stage 4
    eligible_types: list[ItemType]   # which types can honestly test this topic
    requires_context: bool           # true if a single sentence cannot test it
    sibling_group: str | None        # children of a split; never adjacent
    derived_from: str | None         # parent topic id, for inventory coverage
    split_into: list[str]            # non-empty makes this a lineage node:
                                     # never scheduled, never generated for
    split_axis: str | None           # the morph feature this node was split on
    rule_hint: str                   # static one-line form, hint level 3
    intro_card: str                  # static, one phone screen, shown at first contact

class Taxonomy(BaseModel):
    topics: dict[str, Topic]
    def topological_order(self) -> list[str]: ...
    def transitive_prereqs(self, topic_id: str) -> set[str]: ...
    def descendants(self, topic_id: str) -> set[str]: ...
```

`morph_spec` does double duty. Beyond the stage 4 morphology check it defines **facets**: the features it leaves unspecified are exactly the dimensions a topic varies over. A topic fixing `{"Case": "Dat"}` varies over Gender, Number and article type, so those form its facet space. Facets are therefore derived, never hand-written for 85 topics.

A topic with an empty `morph_spec` has no facets, and the facet requirement in stage 6 degrades explicitly rather than silently.

`eligible_types` is a **correctness constraint, not a stylistic preference**.

**The solvability rule.** A topic may declare `cloze_free` only if the carrier sentence can be made to *force* the answer. Delete the target word and ask what still requires it. If the answer is "nothing", the item cannot be solved by reasoning, only guessed, and no prompt engineering repairs it. `Morgen ___ die Sonne scheinen.` is solvable, because *Morgen* plus the sentence-final infinitive force the future auxiliary. `___ Hund schläft im Garten.` is not, because nothing forces a definite article over an indefinite or a possessive.

This bites hardest where the prompt may not name the topic, which is everywhere in this product, so the sentence has to carry the whole constraint. Four ways to supply it, one per topic:

1. **Discourse forces it** (`requires_context: true`, paragraph cloze). Definiteness, demonstrative choice and evidential modality are discourse properties that no single sentence can fix. Applies to `artikel_bestimmt_nom`, `artikel_unbestimmt_kein_nom`, `artikel_possessiv_nom`, `pronomen_demonstrativ_indefinit`, `modalverben_subjektiv_vermutung`, `modalverben_subjektiv_behauptung`.
2. **A lexical cue forces it** (`cloze_cued`). The grammar is determined but the lexeme is not, so the lemma is given in brackets and the learner supplies the form. The cue names a word, never a grammatical category, so the no-terminology rule holds. Applies to the plural, comparison, present-tense, past-tense, imperative and attributive-participle topics.
3. **A source form forces it** (`transformation`). Word-order and style topics have no single gap that can carry the answer.
4. **An element of the carrier forces it** (`cloze_free` retained). The spec sheet must then name that element, and generation must require it to be present: a temporal anchor for every tense and passive topic, a motion or location verb for the two-way prepositions, an unambiguous antecedent for relative clauses, the governing word for the fixed-preposition topics.

The full per-topic assignment is recorded in `docs/audits/stage-04-pilot-2026-08-15.md`. A topic whose declared types do not match its solvability is a taxonomy defect, not a generation problem. There is no global item-type quota anywhere in the system; the observed distribution is emergent. Roughly 8 to 12 topics will carry `requires_context: true` (Konjunktiv I, backward-referring connectors, da-compounds, Plusquamperfekt, Nominalstil and register at B2), and those are paragraph-cloze only.

`rule_hint` is static text, so hint level 3 costs nothing at runtime. Only levels beyond it, and only on request, ever reach an LLM.

`intro_card` is the full first-contact explanation. **[AGENT-CRITICAL]** Generated once on a capable tier, second-agent checked against the structural tests below, then frozen into the taxonomy. It is never generated at runtime and never shown during interleaved review, where it would leak the topic.

`morph_spec` is the bridge to stage 4. A topic that cannot express its target as morphological features (word order topics, for example) sets it to `{}` and is excluded from the morphology check, which must be explicit rather than silent.

### Tests

```python
def test_dag_is_acyclic()
    # topological_order() succeeds; no cycle

def test_every_prereq_id_exists()

def test_no_prereq_has_higher_cefr_than_its_dependent()
    # a B2 topic may depend on A1; the reverse is a taxonomy error

def test_every_topic_id_is_snake_case_and_unique()

def test_every_confusion_group_has_at_least_two_members()
    # a group of one cannot produce interleaved contrast

def test_topic_ids_match_golden_file()
    # data/fixtures/taxonomy/expected_ids.json
    # IDs are referenced by generated items and user progress rows.
    # A silent rename orphans a user's history. Changing this fixture
    # requires an explicit migration note in the commit body.

def test_every_cefr_level_has_at_least_ten_topics()
    # catches a level whose PDF extraction silently failed

def test_transitive_prereqs_terminates_and_excludes_self()

def test_every_topic_has_at_least_one_eligible_type()

def test_requires_context_topics_are_paragraph_cloze_eligible()
    # a topic that needs context but cannot be tested by paragraph cloze
    # is untestable, which is a taxonomy defect

def test_requires_context_topics_exclude_single_sentence_types()
    # Konjunktiv I must not be marked eligible for cloze_free.
    # This is the assertion that stops a single-sentence item from
    # pretending to test indirect speech.

def test_every_topic_has_a_non_empty_rule_hint()

def test_rule_hint_is_not_the_answer()
    # the hint states the rule; it must not give the form away,
    # or level 3 collapses into level 4

def test_every_topic_has_an_intro_card()

def test_intro_card_fits_one_screen()
    # character ceiling. Longer than one phone screen is a
    # textbook chapter, and it will not be read.

def test_intro_card_contains_at_least_two_worked_examples()

def test_intro_card_of_a_grouped_topic_names_its_confusion_sibling()
    # introducing Dativ after a Wechselpräposition without Akkusativ
    # beside it teaches half a rule

def test_rule_hint_is_consistent_with_intro_card()
    # golden review: the one-line hint must not contradict the card

def test_lineage_nodes_are_never_schedulable()
    # split_into non-empty means the node is history only

def test_children_inherit_parent_prereqs_and_cefr()

def test_dependents_of_a_split_parent_require_all_children()
    # the parent was previously required in full; splitting it
    # did not change what depends on it

def test_dag_remains_acyclic_after_a_split()

def test_child_morph_spec_is_parent_plus_the_split_axis_fixed()
    # the split axis moves from varying facet to fixed feature,
    # which shrinks the child's facet space and makes recursive
    # splitting coherent

def test_goethe_coverage_resolves_through_derived_from()
    # inventory entries map to lineages, not leaf ids, so the
    # external validation survives splitting

def test_facet_space_derivable_from_morph_spec()
    # for every topic with non-empty morph_spec, the unspecified
    # features must yield at least 2 possible facet values, or the
    # topic can never satisfy the promotion rule

def test_empty_morph_spec_topics_declare_no_facets_explicitly()
    # must be an explicit empty facet space, not an absent field

def test_morph_spec_keys_are_valid_universal_dependencies_features()
    # reject typos like "Kasus" or "Dative" that would silently
    # disable the stage 4 morphology check

@pytest.mark.golden
def test_goethe_inventory_coverage()
    # data/fixtures/taxonomy/goethe_inventory_checklist.yaml lists every
    # grammar entry extracted from the four PDFs. Assert each maps to at
    # least one topic id, or is explicitly marked out_of_scope with a reason.
```

The coverage test is what stops the taxonomy from quietly omitting a third of B2. Build the checklist as you extract; do not reconstruct it afterwards.

### Definition of Done

- [ ] 75 to 90 topics, all four levels represented
- [ ] At least 8 confusion groups defined, including the seven named in the plan
- [ ] Every Goethe inventory entry mapped or explicitly excluded with a reason
- [ ] Golden ID file committed
- [ ] Second-agent diff committed for the DAG and confusion groups, with conflicts listed
- [ ] `morph_spec` present for every topic where morphology applies, `{}` elsewhere
- [ ] `eligible_types` and `requires_context` set for every topic **[AGENT-CRITICAL]**
- [ ] `rule_hint` and `intro_card` written for every topic **[AGENT-CRITICAL]**

---

## Stage 2: Corpus and lexical resources

**Branch:** `stage/02-corpus`

### Goal

Seed sentences and vocabulary banding, so generation produces natural German at a controlled level rather than stilted invented sentences.

### Deliverables

- Tatoeba DE/EN pairs ingested, filtered, licence-attributed
- Leipzig frequency bands loaded, lemma to band lookup
- Goethe A1/A2/B1 wordlists parsed; B2 falls back to frequency band alone
- spaCy `de_core_news_lg` pipeline wrapper with lemma and morph extraction
- Attribution file generated from the ingested sources

### Contract

```python
class SeedSentence(BaseModel):
    id: str
    text_de: str
    text_en: str | None
    lemmas: list[str]
    max_freq_band: int            # highest (rarest) band among content lemmas
    token_count: int
    source: str                   # "tatoeba"
    licence: str                  # "CC-BY-2.0"
    attribution: str

class Lexicon:
    def band(self, lemma: str) -> int | None: ...
    def in_goethe_list(self, lemma: str, level: CEFR) -> bool: ...
    def level_ceiling_ok(self, lemmas: list[str], level: CEFR) -> bool: ...
```

### Filtering rules

Reject seed sentences that are: under 4 or over 18 tokens, contain proper nouns outside a whitelist, contain no finite verb, or fail spaCy parsing.

### Tests

```python
def test_every_seed_sentence_parses_without_error()

def test_every_seed_record_has_non_null_licence_and_attribution()
    # licence hygiene is enforced by the schema, not by memory

def test_token_count_bounds_enforced()

def test_band_lookup_returns_none_for_unknown_lemma_not_zero()
    # a silent 0 would read as "most frequent" and let rare words through

def test_level_ceiling_rejects_above_level_vocabulary()
    # table-driven: known A1 sentence passes at A1;
    # sentence containing a band-6 lemma fails at A1, passes at B2

def test_lemmatisation_handles_separable_verbs()
    # "Ich rufe dich morgen an" -> lemma "anrufen", not "rufen" + "an"
    # this specific failure silently corrupts vocab tagging

def test_umlaut_and_eszett_normalisation_is_lossless()
    # normalise("Straße") round-trips; "Strasse" maps to the same lemma key

def test_attribution_file_lists_every_source_actually_used()

@pytest.mark.live
def test_tatoeba_ingest_end_to_end()
```

The separable-verb test matters more than it looks. German separable verbs are extremely common at A1 to B1, and a lemmatiser that splits them will mis-band a large fraction of the vocabulary layer.

### Definition of Done

- [ ] At least 20,000 seed sentences surviving filters
- [ ] Band coverage above 95% of content lemmas in the seed set
- [ ] `ATTRIBUTION.md` generated, not hand-maintained
- [ ] No non-commercial-restricted data in any artefact intended for publication

---

## Stage 2b: Learner error corpus

**Branch:** `stage/02b-learner-corpus`

### Goal

Real learner errors, mapped onto the taxonomy, to source the error-correction item type and to derive confusion groups empirically.

### Sources

| Source | What | Access |
|---|---|---|
| Falko-MERLIN GEC corpus | ~24,000 sentences, 381,000 words, learner German A1 to C2, wrong and corrected pairs with ERRANT-German edit tags | Hugging Face `matejklemen/falko_merlin` |
| MERLIN | ~1,000 German learner texts, CEFR-rated, error-annotated, CC BY-SA 4.0 | merlin-platform.eu |
| ERRANT-German | Adriane Boyd's fork: automatic German error tagging on a spaCy pipeline | `github.com/adrianeboyd/errant` branch `german` |
| MultiGED | 2,503 German sentences, token-level correct/incorrect | Shared task release |

### The critical limitation

**The corpus does not contain grammar topics.** ERRANT labels edits, not rules. `R:DET:FORM` says a determiner was replaced with a different form of the same lemma. It does not say whether the underlying error was Dativ after a Wechselpräposition, adjective declension, or relative pronoun case.

Mapping ERRANT tags to taxonomy topic IDs is the work of this stage, and it is lossy. Plan for it, do not discover it.

### Expected yield

Published frequency data on the German set puts the most common error types at roughly: punctuation 15%, spelling 14%, other 10%, determiner form 10%, orthography 8%. Close to half the corpus is spelling and punctuation, which map to no grammar topic at all.

**Expect to keep 4,000 to 6,000 usable sentences out of 24,000.** If yield is far above that, the mapping is over-eager and should be distrusted.

### The mapping pipeline

```
1. Filter by ERRANT tag
   keep:    DET:FORM, NOUN:FORM, ADJ:FORM, VERB:FORM, VERB:TENSE,
            PREP, PRON, WO, AUX:FORM
   discard: SPELL, PUNCT, ORTH, OTHER, CONJ

2. Morphological diff
   spaCy-parse the original and corrected token.
   Record which features changed: Case Acc->Dat, Number Sing->Plur,
   Tense Pres->Past, Mood Ind->Sub.

3. Context routing
   Inspect the governing head. A case change under a Wechselpräposition
   routes to a different topic than the same case change under a verb.
   Rules live in data/taxonomy/errant_mapping.yaml [AGENT-CRITICAL],
   one rule per (tag, feature-delta, governor-class) triple.

4. Single-error filter
   Discard any sentence with more than one edit.
   Multi-error sentences violate the one-item-one-tag rule.
```

### Contract

```python
class LearnerErrorItem(BaseModel):
    id: str
    original: str            # the sentence containing the error
    corrected: str
    errant_tag: str
    feature_delta: dict[str, tuple[str, str]]   # {"Case": ("Acc", "Dat")}
    governor_pos: str
    governor_lemma: str
    topic_id: str | None     # None means unmapped, kept for analysis
    cefr: CEFR | None        # from MERLIN rating where available
    source: str
```

### Tests

```python
def test_only_single_edit_sentences_are_retained()

def test_discarded_tags_never_produce_a_topic_mapping()
    # SPELL and PUNCT must map to None, always

def test_mapping_is_deterministic_and_total_over_the_rule_table()
    # every (tag, feature-delta, governor-class) triple in the rule file
    # resolves to exactly one topic_id

def test_unmapped_items_are_retained_with_topic_id_none()
    # dropping them silently hides how much of the corpus is unusable

def test_mapping_yield_within_expected_band()
    # assert 15% <= mapped/total <= 30%.
    # too high means the rules are over-matching, which is the
    # dangerous direction: it mislabels errors and corrupts the
    # confusion-group analysis downstream.

@pytest.mark.golden
def test_hand_labelled_mapping_accuracy()
    # data/fixtures/corpus/mapped_sample.jsonl: 100 sentences mapped
    # to topic ids [AGENT-CRITICAL]. Assert agreement >= 0.85.
    # External anchor: MERLIN's expert annotations, authored
    # independently of this pipeline.
    # Without this the mapping rules are unfalsifiable.

def test_feature_delta_extraction_on_known_pairs()
    # table-driven: ("auf den Tisch" -> "auf dem Tisch")
    # yields {"Case": ("Acc", "Dat")}

def test_cefr_rating_carried_through_from_merlin_metadata()

def test_no_sentence_appears_in_both_error_items_and_seed_sentences()
    # a learner error sentence must never be used as a correct carrier
```

### Downstream uses

**Error-correction items.** Source the 20% error-correction slice from mapped corpus sentences rather than generating them. Real errors have the right distribution; invented errors have a plausible-looking one.

**Empirical confusion groups.** Compute error-type co-occurrence per learner and per CEFR band. Feed the result back into stage 1 as a revision of the `confusion_group` assignments. Second-agent check before adopting; the corpus tells you what co-occurs, not what is pedagogically worth contrasting.

**Stage 4 adversarial fixtures.** Real broken sentences instead of 60 invented ones.

### Definition of Done

- [ ] Corpus ingested, single-error filter applied
- [ ] `errant_mapping.yaml` covering the retained tags **[AGENT-CRITICAL]**
- [ ] 100-sentence mapped fixture at 0.85 agreement or above **[AGENT-CRITICAL]**
- [ ] Yield reported, and within the expected band
- [ ] Confusion-group revision proposed back to stage 1, with a second-agent diff

---

## Stage 2c: Exercise scraping

**Branch:** `stage/02c-scraping`

### Goal

A corpus of human-authored German exercises, for use as generation anchors, distribution baseline, and a supplementary item source.

### Targets

| Site | Coverage | Notes |
|---|---|---|
| schubert-verlag.de | A1 to C2, online exercises and worksheets | Best structured. Per-level index pages enumerate every exercise. |
| mein-deutschbuch.de | A1 to B2, online Übungen | Answers checked client-side, so the key is in the page source. |
| deutsch.lingolia.com | A1 to C1, topic pages with exercises | |
| deutschlernerblog.de | A1 to C2 | |
| sprich-deutsch.de | A1 to C2 | |
| Publisher sites | Schritte International, Menschen, Mittelpunkt | Textbook-aligned, useful for progression ordering |

### Be honest about what this buys

Scraping saves generation cost. It does not save verification cost, and verification is the expensive half. Every scraped item goes through the same stage 4 chain as a generated one: its topic must be mapped to your taxonomy, its answer key confirmed, and its answer set checked for alternatives the original author did not list.

Two further problems specific to scraped material:

- **Topic labels are theirs, not yours.** A page titled "Übungen zum Dativ" covers a different slice than your `dativ_nach_praeposition`. Mapping is agent work with a confidence score; low-confidence mappings are left null rather than guessed.
- **Page context leaks the topic.** The exercise itself is usually fine once stripped of its heading, but the stripping must be verified rather than assumed.

The highest-value use is not bulk items. It is **20 to 30 human-authored items per topic family, selected as gold examples for spec sheets**, so the generator imitates real exercise style rather than inventing a stiff one.

The second-highest is a **distribution baseline**: compare generated items against scraped ones on sentence length, clause count, vocabulary band and structure. A statistically distinguishable generator is a quality signal worth acting on.

### Contract

```python
class ScrapedItem(BaseModel):
    id: str
    source_site: str
    source_url: str
    source_topic_label: str        # their label, verbatim
    mapped_topic_id: str | None    # your taxonomy; null when confidence is low
    cefr_claimed: CEFR | None
    prompt_raw: str
    prompt_stripped: str           # heading and instruction removed
    answer_key: list[str]
    exercise_type: str
    scraped_at: datetime
```

### Scraper conventions

- Respect `robots.txt`, rate-limit to roughly one request per second, set a descriptive user agent.
- Cache raw HTML to disk. Never re-request during parser development.
- Parsers are per-site modules with their own fixture HTML files, so a site redesign breaks one parser and one test, not the pipeline.

### Tests

```python
@pytest.mark.golden
def test_parser_against_frozen_html_fixture()
    # one saved page per site in data/fixtures/scraping/.
    # site redesigns are detected as a failing parser test,
    # not as silently empty output.

def test_stripped_prompt_contains_no_grammar_terminology()
    # the same blocklist used in stage 3.
    # a scraped item that still names its topic is unusable.

def test_answer_key_extraction_matches_hand_labelled_sample()
    # 50 items per site, checked against the site's own answer key

def test_unmapped_scraped_items_are_retained_not_dropped()

def test_scraper_respects_rate_limit()
    # fake clock; assert minimum inter-request interval

def test_scraper_reads_from_cache_when_available()
    # a parser test must never hit the network

def test_scraped_items_pass_the_same_verification_chain_as_generated()
    # no separate, softer path for human-authored content

def test_source_url_retained_for_every_item()
```

### Definition of Done

- [ ] At least two site parsers working with frozen HTML fixtures
- [ ] 20 to 30 selected gold examples per topic family, wired into spec sheets
- [ ] Distribution comparison report: generated versus scraped, on length, clause count and vocabulary band
- [ ] Scraped items routed through the unmodified stage 4 chain
