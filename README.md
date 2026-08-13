# Interleaved German Grammar Trainer (A1–B2)

An offline-first, installable German grammar learning tool built around cognitive science principles of **interleaving** and **spaced repetition**.

---

## Core Thesis

Standard language learning applications practice **blocked**: ten Dativ questions in a row, followed by ten Perfekt questions. The learner knows which rule applies before reading the sentence, bypassing the most critical and challenging phase of language processing: **selecting the right rule under uncertainty**.

This application practices **interleaved**:
1. **Prompts Never Name the Topic**: No "Setze ins Dativ" hints. The learner must diagnose the required structure directly from context.
2. **Spaced Repetition over Grammar Topics**: Utilizes the modern **FSRS** (Free Spaced Repetition Scheduler) algorithm over grammar topics rather than static card IDs.
3. **Demand-Driven Content Supply**: Exercises are generated and verified via structured LLM pipelines to prevent sentence memorization, dynamically scaling where the learner's actual frontier sits.
4. **Deterministic Answering Path**: Answering an item requires zero runtime LLM calls, grading with scoped typo-tolerance, case-sensitivity, and morphological checks in milliseconds offline.

---

## System Architecture

- **Offline Pipeline (Python)**:
  - Lexical parsing, Tatoeba sentence seeding, and Falko-MERLIN error mining.
  - Multi-step verification chain (schema, topic-leak detection, Gemini 3.7 Flash answer-set expansion, spaCy morphology checking, vocabulary ceiling limits, and embedding deduplication).
  - SQLite bank storage with strict integrity guarantees and delta JSON export.
- **Learning Engine (Python / TypeScript Parity)**:
  - FSRS scheduling engine with daily introduction budgets and 7-day forecast load gating.
  - Adaptive *Kalibrierung* initial diagnostic assessment.
  - Evidence-driven topic promotion (requiring consecutive unhinted passes across multiple grammatical facets).
- **Client Application (PWA)**:
  - Installable, offline-capable Progressive Web App on Cloudflare Pages.
  - Client-side `ts-fsrs` and IndexedDB persistence.
  - Rich UX retention surfaces: diff highlighting, "Correct, but..." feedback, grammar coverage bars, streak freezes, and interactive DAG topic maps.
- **Edge Layer (Cloudflare Worker & D1)**:
  - Lightweight append-only `review_log` sync.
  - Asynchronous answer override verification.
  - Optional live features: production rubric grading and error-specific diagnostic explanations.

---

## Development Roadmap & Stages

| Stage | Name | Key Deliverable | Gate |
|---|---|---|---|
| **0** | Repo scaffold & CI | Environment (`uv`), `contracts.py`, test suite | - |
| **1** | Taxonomy & DAG | `taxonomy.yaml` with 75–90 topics & prereq graph | - |
| **2** | Corpus & Lexicon | Goethe wordlists, Leipzig frequencies, Tatoeba seeds | - |
| **2b** | Learner Error Corpus | Falko-MERLIN error mapping & confusion seeds | - |
| **2c** | Scraped Exercises | Human-authored exercise baseline & anchor keys | - |
| **3** | Batch Generation | Per-topic generation spec sheets & Gemini batch client | - |
| **4** | Verification Chain | Multi-step rejection chain & adversarial fixtures | **KILL GATE** (Error rate $\le 15\%$) |
| **5** | Bank Storage | SQLite bank & browser-consumable delta export | - |
| **6** | Learning Engine | FSRS scheduler, Kalibrierung, simulation harness | - |
| **7** | CLI & Grading | Terminal client, scoped typo grading | **USABILITY GATE** (2 weeks daily trial) |
| **8** | Offline PWA | Static PWA, `ts-fsrs`, IndexedDB, 11 UX surfaces | - |
| **9** | Worker, D1, Sync | Cloudflare Worker, D1 sync, override verification | - |
| **10** | Nightly Automation | GitHub Actions 2-phase batch top-up jobs | - |
| **11** | Live LLM Features | Production grading, explanations, minimal pairs | - |

---

## Getting Started

### Prerequisites
- Python 3.11+
- [`uv`](https://github.com/astral-sh/uv) (fast Python package manager)
- Git

### Setup Environment
```bash
# Clone the repository
git clone <repo-url>
cd Language_Learning_App

# Create virtual environment and install dependencies with uv
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
uv sync
```

### Running Tests
```bash
pytest
```

---

## Contributing & Development Rules
See [COMMIT_RULES.md](COMMIT_RULES.md) for branch naming (`stage/<stage-name>`), commit standards, and Definition of Done requirements.
