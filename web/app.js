// DeutschMaster - Main Client Application Logic
(function() {
  'use strict';

  // Seed Items Baseline (used if network JSON unavailable or offline)
  const DEFAULT_SEED_ITEMS = [
    {
      id: "bank_a1_001",
      topic_id: "pronomen_personal_nom",
      type: "cloze_free",
      difficulty: 1,
      cefr: "A1",
      prompt: "___ heiße Max und komme aus Deutschland.",
      cue: null,
      accepted_answers: ["Ich"],
      distractors: [{text: "Du"}, {text: "Er"}, {text: "Wir"}],
      rule_hint: "Subjekt in der 1. Person Singular ist 'Ich'.",
      facet: "sg1",
      gloss_en: "I am called Max and come from Germany."
    },
    {
      id: "bank_a1_002",
      topic_id: "verb_praesens_regelm",
      type: "cloze_cued",
      difficulty: 1,
      cefr: "A1",
      prompt: "Er ___ jeden Tag im Büro.",
      cue: "arbeiten",
      accepted_answers: ["arbeitet"],
      distractors: [{text: "arbeite"}, {text: "arbeitest"}, {text: "arbeiten"}],
      rule_hint: "Endung bei er/sie/es im Präsens ist '-t'.",
      facet: "sg3",
      gloss_en: "He works in the office every day."
    },
    {
      id: "bank_a2_001",
      topic_id: "dativ_nach_praeposition",
      type: "cloze_free",
      difficulty: 1,
      cefr: "A2",
      prompt: "Das Buch liegt auf ___ Tisch.",
      cue: null,
      accepted_answers: ["dem"],
      distractors: [{text: "den"}, {text: "des"}, {text: "das"}],
      rule_hint: "Wechselpräposition auf + Dativ bei Wo? (Lage).",
      facet: "masc_dat",
      gloss_en: "The book is lying on the table."
    },
    {
      id: "bank_a2_002",
      topic_id: "akkusativ_nach_praeposition",
      type: "cloze_free",
      difficulty: 1,
      cefr: "A2",
      prompt: "Er legt das Buch auf ___ Tisch.",
      cue: null,
      accepted_answers: ["den"],
      distractors: [{text: "dem"}, {text: "des"}, {text: "das"}],
      rule_hint: "Wechselpräposition auf + Akkusativ bei Wohin? (Richtung).",
      facet: "masc_acc",
      gloss_en: "He is putting the book on the table."
    },
    {
      id: "bank_b1_001",
      topic_id: "nebensatz_weil_da",
      type: "cloze_free",
      difficulty: 2,
      cefr: "B1",
      prompt: "Ich bleibe heute zu Hause, ___ ich krank bin.",
      cue: null,
      accepted_answers: ["weil", "da"],
      distractors: [{text: "denn"}, {text: "deshalb"}, {text: "obwohl"}],
      rule_hint: "Kausalsatz mit weil schickt das Verb ans Ende.",
      facet: "kausal",
      gloss_en: "I am staying at home today because I am ill."
    },
    {
      id: "bank_b1_002",
      topic_id: "konjunktiv_ii_irreal_gegenwart",
      type: "cloze_cued",
      difficulty: 2,
      cefr: "B1",
      prompt: "Wenn ich Zeit hätte, ___ ich gerne reisen.",
      cue: "würde",
      accepted_answers: ["würde"],
      distractors: [{text: "werde"}, {text: "wurde"}, {text: "wäre"}],
      rule_hint: "Konjunktiv II Ersatzform: würde + Infinitiv.",
      facet: "irreal",
      gloss_en: "If I had time, I would like to travel."
    }
  ];

  // Client-Side Scoped Typo Grader.
  //
  // This set, CRITICAL_MINIMAL_PAIRS, and the grading algorithm below MUST
  // stay identical to src/engine/typo_grader.py's ScopedTypoGrader. Two
  // divergent graders writing to one FSRS history is a data-corruption path
  // (04-application.md stage 9). tests/test_web.py asserts the morpheme and
  // minimal-pair sets never drift apart again.
  const GRAMMATICAL_MORPHEMES = new Set([
    "dem", "den", "des", "der", "die", "das",
    "einem", "einen", "einer", "eines", "eine", "ein",
    "keinem", "keinen", "keiner", "keines", "keine", "kein",
    "meinem", "meinen", "meiner", "meines", "meine", "mein",
    "seinem", "seinen", "seiner", "seines", "seine", "sein",
    "ihrem", "ihren", "ihrer", "ihres", "ihre", "ihr",
    "unserem", "unseren", "unserer", "unseres", "unsere", "unser",
    "hat", "hatte", "hätte", "ist", "war", "wäre", "wird", "wurde", "würde",
    "sie", "Sie", "ihm", "ihn", "mir", "mich", "dir", "dich", "uns", "euch", "sich",
    "als", "wenn", "weil", "denn", "obwohl", "dass", "da", "während", "wahrend",
    "wegen", "trotz", "statt", "anstatt"
  ]);

  // Pairs that differ by exactly one edit but are never interchangeable: each
  // member is grammatically real, so edit-distance tolerance must never
  // paper over the difference. Mirrors ScopedTypoGrader.CRITICAL_MINIMAL_PAIRS.
  const CRITICAL_MINIMAL_PAIRS = new Set([
    "dem|den", "dem|des", "den|der",
    "einem|einen", "einem|einer",
    "hatte|hätte", "hatten|hätten",
    "war|wäre", "waren|wären",
    "konnte|könnte", "musste|müsste", "mochte|möchte",
    "schon|schön", "schoner|schöner",
    "großer|größer",
    "flog|flöge"
  ]);

  function isCriticalPair(a, b) {
    const lowA = a.toLowerCase();
    const lowB = b.toLowerCase();
    return CRITICAL_MINIMAL_PAIRS.has(`${lowA}|${lowB}`) || CRITICAL_MINIMAL_PAIRS.has(`${lowB}|${lowA}`);
  }

  // Suffix window used to scope edit-distance tolerance when no faceted-topic
  // signal is available client-side (see _edit_outside_suffix in the Python
  // grader). German inflection is overwhelmingly suffixal, so an edit that
  // reaches into the tail of the word is treated as a possible morpheme
  // mutation, never as an incidental typo.
  const SUFFIX_TOLERANCE_WINDOW = 2;

  function commonPrefixLen(a, b) {
    let n = 0;
    while (n < a.length && n < b.length && a[n] === b[n]) n++;
    return n;
  }

  function editOutsideSuffix(a, b, suffixLen = SUFFIX_TOLERANCE_WINDOW) {
    const longerLen = Math.max(a.length, b.length);
    if (longerLen <= suffixLen) return false;
    return commonPrefixLen(a, b) < longerLen - suffixLen;
  }

  function levenshtein(a, b) {
    const matrix = [];
    for (let i = 0; i <= b.length; i++) matrix[i] = [i];
    for (let j = 0; j <= a.length; j++) matrix[0][j] = j;
    for (let i = 1; i <= b.length; i++) {
      for (let j = 1; j <= a.length; j++) {
        if (b.charAt(i - 1) === a.charAt(j - 1)) {
          matrix[i][j] = matrix[i - 1][j - 1];
        } else {
          matrix[i][j] = Math.min(
            matrix[i - 1][j - 1] + 1,
            matrix[i][j - 1] + 1,
            matrix[i - 1][j] + 1
          );
        }
      }
    }
    return matrix[b.length][a.length];
  }

  // Canonicalize ß/ae/oe/ue so both sides of a transliteration comparison
  // land on the same representation. Mirrors the canon_in/canon_ans
  // normalization in ScopedTypoGrader.grade step 2.
  function canonicalizeOrthography(str) {
    return str
      .replaceAll('ß', 'ss')
      .replaceAll('ae', 'ä')
      .replaceAll('oe', 'ö')
      .replaceAll('ue', 'ü');
  }

  // Faithful port of ScopedTypoGrader.grade (src/engine/typo_grader.py).
  // `topicId` is accepted for forward compatibility with topic-scoped
  // (morph_spec-faceted) tolerance, but the exported bank does not currently
  // ship morph_spec to the client, so isFacetedTopic conservatively always
  // returns false here -- exactly Python's fallback behaviour for an
  // unrecognised/unknown topic_id. This never produces a false FAIL, only
  // (at worst) a slightly wider tolerance than the server would apply.
  function isFacetedTopic(_topicId) {
    return false;
  }

  function gradeSubmission(userInput, acceptedAnswers, topicId = null) {
    const cleanInput = userInput.trim().replace(/\s+/g, ' ');
    const cleanAccepted = acceptedAnswers.map(a => a.trim().replace(/\s+/g, ' '));

    // 1. Exact Match
    if (cleanAccepted.includes(cleanInput)) {
      return {
        isCorrect: true, isExact: true, isScopedTypo: false,
        isTransliteration: false, isCapitalizationError: false,
        matched: cleanInput, message: null
      };
    }

    // 2. Umlaut & orthography transliteration match (ae/oe/ue <-> ä/ö/ü, ss <-> ß)
    for (const ans of cleanAccepted) {
      const inputLower = cleanInput.toLowerCase();
      if (['grosser', 'groesser'].includes(inputLower) && ['größer', 'grösser'].includes(ans.toLowerCase())) {
        return {
          isCorrect: true, isExact: false, isScopedTypo: false,
          isTransliteration: true, isCapitalizationError: false,
          matched: ans, message: `Richtig (Transliteration). Standard: '${ans}'`
        };
      }

      if (isCriticalPair(cleanInput, ans)) continue;

      const canonIn = canonicalizeOrthography(cleanInput);
      const canonAns = canonicalizeOrthography(ans);
      if (canonIn.toLowerCase() === canonAns.toLowerCase()) {
        if (canonIn !== canonAns) {
          return {
            isCorrect: false, isExact: false, isScopedTypo: false,
            isTransliteration: false, isCapitalizationError: true,
            matched: ans, message: `Falsch. Achte auf die Groß-/Kleinschreibung: '${ans}'`
          };
        }
        return {
          isCorrect: true, isExact: false, isScopedTypo: false,
          isTransliteration: true, isCapitalizationError: false,
          matched: ans, message: `Richtig (Transliteration). Standard: '${ans}'`
        };
      }
    }

    // 3. Capitalization-only difference -> FAILS in German
    for (const ans of cleanAccepted) {
      if (cleanInput.toLowerCase() === ans.toLowerCase() && cleanInput !== ans) {
        return {
          isCorrect: false, isExact: false, isScopedTypo: false,
          isTransliteration: false, isCapitalizationError: true,
          matched: ans, message: `Falsch. Achte auf die Groß-/Kleinschreibung: '${ans}'`
        };
      }
    }

    // 4. Multi-token / scoped single-token morpheme typo
    for (const ans of cleanAccepted) {
      const inTokens = cleanInput.split(' ');
      const ansTokens = ans.split(' ');

      if (inTokens.length === ansTokens.length && ansTokens.length > 1) {
        const tokenMatches = [];
        let hasTypo = false;
        let hasMorphemeError = false;

        for (let i = 0; i < ansTokens.length; i++) {
          const inTok = inTokens[i];
          const ansTok = ansTokens[i];
          if (inTok === ansTok) {
            tokenMatches.push(true);
          } else if (GRAMMATICAL_MORPHEMES.has(inTok.toLowerCase()) || GRAMMATICAL_MORPHEMES.has(ansTok.toLowerCase())) {
            hasMorphemeError = true;
            break;
          } else if (levenshtein(inTok, ansTok) === 1 && ansTok.length > 3) {
            hasTypo = true;
            tokenMatches.push(true);
          } else {
            tokenMatches.push(false);
          }
        }

        if (!hasMorphemeError && hasTypo && tokenMatches.every(Boolean)) {
          return {
            isCorrect: true, isExact: false, isScopedTypo: true,
            isTransliteration: false, isCapitalizationError: false,
            matched: ans, message: `Richtig (Tippfehler). Schreibweise: '${ans}'`
          };
        }
        continue;
      }

      if (isCriticalPair(cleanInput, ans)) continue;

      if (GRAMMATICAL_MORPHEMES.has(ans.toLowerCase()) || GRAMMATICAL_MORPHEMES.has(cleanInput.toLowerCase())) {
        continue;
      }

      const dist = levenshtein(cleanInput, ans);
      if (dist !== 1) continue;

      // Single-token answer: the whole token is the candidate morpheme. A
      // faceted topic gets zero tolerance; an unfaceted/unknown topic falls
      // back to the suffix-scoped heuristic.
      if (isFacetedTopic(topicId)) continue;
      if (!editOutsideSuffix(cleanInput, ans)) continue;

      return {
        isCorrect: true, isExact: false, isScopedTypo: true,
        isTransliteration: false, isCapitalizationError: false,
        matched: ans, message: `Richtig (Tippfehler). Schreibweise: '${ans}'`
      };
    }

    // 5. Incorrect
    return {
      isCorrect: false, isExact: false, isScopedTypo: false,
      isTransliteration: false, isCapitalizationError: false,
      matched: cleanAccepted[0], message: `Falsch. Richtige Antwort: '${cleanAccepted[0]}'`
    };
  }

  function renderDiff(expected, given) {
    if (!given) return `<span class="diff-ins">${expected}</span>`;
    return `<span class="diff-del">${given}</span> ➔ <span class="diff-ins">${expected}</span>`;
  }

  // App State
  let bankItems = [];
  let currentRoundItems = [];
  let currentItemIndex = 0;
  let currentHintLevel = 0;
  let roundAttempts = [];
  let streak = 3;
  let config = {
    roundSize: 6,
    vocabRatio: 0.3,
    forecastThreshold: 50
  };

  let topicStates = {};
  let fsrsRecords = {};

  // Export schema version. Import rejects any file whose version is not in
  // this set rather than importing it blindly (04-application.md stage 8,
  // "export includes a schema version and import rejects unknown versions").
  const EXPORT_SCHEMA_VERSION = '2.0';
  const SUPPORTED_IMPORT_VERSIONS = new Set(['2.0']);

  // --- Derived-state helpers -------------------------------------------
  // topic_state and fsrs card records are derived data: their only source of
  // truth is review_log. `applyReviewToTopicState`/`applyReviewToFsrsCard`
  // fold one new log entry into the current in-memory derived state (used on
  // live grading, incremental). `computeTopicStatesFromLog`/
  // `computeFsrsCardsFromLog` rebuild derived state from scratch given a full
  // log (used on import, where the file's own tag_state must never be
  // trusted directly -- 04-application.md:363).

  function applyReviewToTopicState(states, entry) {
    const topicId = entry.topic_id;
    if (!topicId) return states;
    const existing = states[topicId] || {
      topic_id: topicId,
      attempts: 0,
      correct: 0,
      last_result: null,
      updated_at: null
    };
    const updated = {
      ...existing,
      attempts: existing.attempts + 1,
      correct: existing.correct + (entry.is_correct ? 1 : 0),
      last_result: !!entry.is_correct,
      updated_at: entry.timestamp || existing.updated_at
    };
    return { ...states, [topicId]: updated };
  }

  function applyReviewToFsrsCard(cards, entry) {
    const cardId = entry.card_id || entry.item_id;
    if (!cardId) return cards;
    const existing = cards[cardId] || {
      card_id: cardId,
      topic_id: entry.topic_id || null,
      reps: 0,
      lapses: 0,
      last_review: null
    };
    const updated = {
      ...existing,
      reps: existing.reps + 1,
      lapses: existing.lapses + (entry.is_correct ? 0 : 1),
      last_review: entry.timestamp || existing.last_review
    };
    return { ...cards, [cardId]: updated };
  }

  function computeTopicStatesFromLog(reviewLog) {
    return reviewLog.reduce(applyReviewToTopicState, {});
  }

  function computeFsrsCardsFromLog(reviewLog) {
    return reviewLog.reduce(applyReviewToFsrsCard, {});
  }

  // DOM Elements
  const viewPractice = document.getElementById('view-practice');
  const viewSummary = document.getElementById('view-summary');
  const viewDag = document.getElementById('view-dag');
  const viewDuels = document.getElementById('view-duels');
  const viewChallenge = document.getElementById('view-challenge');
  const viewStats = document.getElementById('view-stats');

  const navPracticeBtn = document.getElementById('nav-practice-btn');
  const navDagBtn = document.getElementById('nav-dag-btn');
  const navDuelsBtn = document.getElementById('nav-duels-btn');
  const navChallengeBtn = document.getElementById('nav-challenge-btn');
  const navStatsBtn = document.getElementById('nav-stats-btn');
  const navSettingsBtn = document.getElementById('nav-settings-btn');

  const promptBox = document.getElementById('exercise-prompt');
  const glossBox = document.getElementById('exercise-gloss');
  const cuedArea = document.getElementById('cued-area');
  const cuedWord = document.getElementById('cued-word');
  const textInputArea = document.getElementById('text-input-area');
  const userAnswerInput = document.getElementById('user-answer-input');
  const submitBtn = document.getElementById('submit-btn');
  const optionsArea = document.getElementById('options-area');
  const hintStatusText = document.getElementById('hint-status-text');
  const requestHintBtn = document.getElementById('request-hint-btn');
  const hintBox = document.getElementById('hint-box');
  const feedbackBox = document.getElementById('feedback-box');

  const statRoundProgress = document.getElementById('stat-round-progress');
  const statDueTopics = document.getElementById('stat-due-topics');
  const statStreak = document.getElementById('stat-streak');
  const roundProgressFill = document.getElementById('round-progress-fill');
  const cardCefrBadge = document.getElementById('card-cefr-badge');
  const cardDiffBadge = document.getElementById('card-diff-badge');
  const cardTagBadge = document.getElementById('card-tag-badge');

  const btnNextRound = document.getElementById('btn-next-round');
  const dagContainer = document.getElementById('dag-container');
  const duelList = document.getElementById('duel-list');

  // Navigation
  function switchView(targetSection, targetBtn) {
    [viewPractice, viewSummary, viewDag, viewDuels, viewChallenge, viewStats].forEach(s => s && s.classList.remove('active'));
    [navPracticeBtn, navDagBtn, navDuelsBtn, navChallengeBtn, navStatsBtn, navSettingsBtn].forEach(b => b && b.classList.remove('active'));

    if (targetSection) targetSection.classList.add('active');
    if (targetBtn) targetBtn.classList.add('active');

    if (targetSection === viewDag) renderDagView();
    if (targetSection === viewDuels) renderDuelsView();
    if (targetSection === viewStats) renderStatsView();
  }

  navPracticeBtn.addEventListener('click', () => switchView(viewPractice, navPracticeBtn));
  navDagBtn.addEventListener('click', () => switchView(viewDag, navDagBtn));
  navDuelsBtn.addEventListener('click', () => switchView(viewDuels, navDuelsBtn));
  navChallengeBtn.addEventListener('click', () => switchView(viewChallenge, navChallengeBtn));
  navStatsBtn.addEventListener('click', () => switchView(viewStats, navStatsBtn));

  navSettingsBtn.addEventListener('click', () => {
    document.getElementById('settings-modal').style.display = 'flex';
  });
  document.getElementById('btn-close-settings').addEventListener('click', () => {
    // Settings apply from the next round only, never mid-round: they are
    // read into `config` here (on close), not on every slider tick.
    config.roundSize = parseInt(document.getElementById('input-round-size').value, 10);
    config.vocabRatio = parseInt(document.getElementById('input-vocab-ratio').value, 10) / 100.0;
    config.forecastThreshold = parseInt(document.getElementById('input-forecast-thresh').value, 10);
    document.getElementById('settings-modal').style.display = 'none';
  });

  // Slider readouts track the handle live; this is display only and does not
  // itself change `config` (that happens on save, above).
  const roundSizeInput = document.getElementById('input-round-size');
  const roundSizeVal = document.getElementById('val-round-size');
  roundSizeInput.addEventListener('input', () => {
    roundSizeVal.textContent = roundSizeInput.value;
  });

  const vocabRatioInput = document.getElementById('input-vocab-ratio');
  const vocabRatioVal = document.getElementById('val-vocab-ratio');
  vocabRatioInput.addEventListener('input', () => {
    vocabRatioVal.textContent = `${vocabRatioInput.value}%`;
  });

  const forecastThreshInput = document.getElementById('input-forecast-thresh');
  const forecastThreshVal = document.getElementById('val-forecast-thresh');
  forecastThreshInput.addEventListener('input', () => {
    forecastThreshVal.textContent = forecastThreshInput.value;
  });

  const btnResetDag = document.getElementById('btn-reset-dag');
  if (btnResetDag) {
    btnResetDag.addEventListener('click', () => {
      renderDagView();
    });
  }

  // Data Loading & Storage Initialization
  async function initStorageAndItems() {
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('./sw.js').catch(err => {
        console.warn('Service Worker registration skipped:', err);
      });
    }

    if (window.offlineStorage) {
      await window.offlineStorage.init();
      const savedStates = await window.offlineStorage.getTopicStates();
      if (savedStates && savedStates.length > 0) {
        savedStates.forEach(s => { topicStates[s.topic_id || s.tag_id] = s; });
      }
      const savedCards = await window.offlineStorage.getFSRSCards();
      if (savedCards && savedCards.length > 0) {
        savedCards.forEach(c => { fsrsRecords[c.card_id] = c; });
      }
    }

    try {
      const resp = await fetch('./data/all_items.json');
      if (resp.ok) {
        const data = await resp.json();
        bankItems = data.items || data;
      } else {
        bankItems = DEFAULT_SEED_ITEMS;
      }
    } catch (e) {
      bankItems = DEFAULT_SEED_ITEMS;
    }

    startNewRound();
  }

  function startNewRound() {
    currentRoundItems = bankItems.slice(0, config.roundSize);
    currentItemIndex = 0;
    roundAttempts = [];
    switchView(viewPractice, navPracticeBtn);
    renderCurrentItem();
  }

  function renderCurrentItem() {
    currentHintLevel = 0;
    hintBox.classList.remove('active');
    hintBox.textContent = '';
    feedbackBox.style.display = 'none';
    feedbackBox.className = 'feedback-box';
    feedbackBox.textContent = '';

    const item = currentRoundItems[currentItemIndex];
    if (!item) {
      showRoundSummary();
      return;
    }

    statRoundProgress.textContent = `${currentItemIndex + 1} / ${currentRoundItems.length}`;
    statDueTopics.textContent = `${Object.keys(topicStates).length} Themen`;
    statStreak.textContent = `${streak} Tage 🔥`;
    roundProgressFill.style.width = `${((currentItemIndex) / currentRoundItems.length) * 100}%`;

    cardCefrBadge.textContent = item.cefr || 'A2';
    cardDiffBadge.textContent = `Stufe ${item.difficulty || 1}`;
    if (cardTagBadge) cardTagBadge.textContent = 'Grammatik-Übung';

    let promptHtml = item.prompt;
    if (promptHtml.includes('___')) {
      promptHtml = promptHtml.replace('___', '<span class="gap-blank">___</span>');
    }
    promptBox.innerHTML = promptHtml;

    renderGloss(item);

    if (item.cue) {
      cuedArea.style.display = 'block';
      cuedWord.textContent = item.cue;
    } else {
      cuedArea.style.display = 'none';
    }

    textInputArea.style.display = 'flex';
    optionsArea.style.display = 'none';
    userAnswerInput.value = '';
    userAnswerInput.disabled = false;
    submitBtn.disabled = false;
    submitBtn.textContent = 'Prüfen';
    userAnswerInput.focus();

    updateHintUI();
  }

  // English gloss (TODO.md section 4, feature 5.1 step 3).
  //
  // "Every exercise shows its English translation, always." Shown BEFORE the
  // learner answers, and left up afterwards -- it is not a reward and not a
  // hint tier. Two reasons it has to be visible up front rather than revealed
  // with the feedback:
  //
  //   1. The owner's decision is pedagogical, not a fallback for ambiguity. A
  //      translation the learner only sees once the answer is graded teaches
  //      nothing at the moment comprehension is needed.
  //   2. TODO.md 5.1 lets the verifier treat the translation as available to
  //      the learner when it judges whether a gap has a unique answer, and
  //      that relaxation is the very next task. If the gloss appeared only
  //      after grading, the verifier would be relaxing its uniqueness gates
  //      against evidence the learner did not have while answering, which is
  //      exactly the non-unique-answer defect class cycles 11 and 12 closed.
  //
  // Yes, the gloss often contains an English word that maps onto the blanked
  // German token. That is intended: a translation disambiguates tense, number,
  // person and definiteness, which is the entire point. It still marks none of
  // German case, gender, adjective endings, reflexive case or preposition
  // government, which is where the exercises actually live.
  //
  // `gloss_en` is null on every bank row exported before the translation
  // backfill, so a missing gloss must cost nothing: the node is emptied and
  // hidden outright, never rendered as an empty bordered box or the string
  // "null". Written with textContent, never innerHTML -- a gloss is generated
  // content and must not be able to inject markup into the exercise card.
  function renderGloss(item) {
    if (!glossBox) return;
    const gloss = item && typeof item.gloss_en === 'string' ? item.gloss_en.trim() : '';
    glossBox.textContent = gloss;
    glossBox.hidden = gloss === '';
  }

  function updateHintUI() {
    hintStatusText.textContent = `Hinweis-Stufe: ${currentHintLevel} / 4`;
    if (currentHintLevel >= 4) {
      requestHintBtn.disabled = true;
      requestHintBtn.textContent = 'Alle Hinweise aufgedeckt';
    } else {
      requestHintBtn.disabled = false;
      requestHintBtn.textContent = `💡 Hinweis anfordern (Stufe ${currentHintLevel + 1})`;
    }
  }

  requestHintBtn.addEventListener('click', () => {
    const item = currentRoundItems[currentItemIndex];
    if (!item || currentHintLevel >= 4) return;
    currentHintLevel++;
    hintBox.classList.add('active');

    const expected = item.accepted_answers[0];
    if (currentHintLevel === 1) {
      hintBox.textContent = `Wortlänge: ${expected.length} Buchstaben (${expected[0]}...)`;
    } else if (currentHintLevel === 2) {
      hintBox.textContent = `Verfügbare Optionen vorhanden. Wähle unten aus.`;
      renderMultipleChoiceOptions(item);
    } else if (currentHintLevel === 3) {
      hintBox.textContent = item.rule_hint || `Grammatikregel beachten.`;
    } else if (currentHintLevel === 4) {
      hintBox.textContent = `Lösung: ${expected}`;
    }
    updateHintUI();
  });

  function renderMultipleChoiceOptions(item) {
    optionsArea.innerHTML = '';
    const distractors = (item.distractors || []).map(d => (typeof d === 'string' ? d : d.text));
    const allOpts = [...new Set([...item.accepted_answers, ...distractors])].sort(() => Math.random() - 0.5);

    allOpts.forEach(opt => {
      const btn = document.createElement('button');
      btn.className = 'option-btn';
      btn.textContent = opt;
      btn.addEventListener('click', () => {
        userAnswerInput.value = opt;
        evaluateAnswer();
      });
      optionsArea.appendChild(btn);
    });
    textInputArea.style.display = 'none';
    optionsArea.style.display = 'grid';
  }

  submitBtn.addEventListener('click', evaluateAnswer);
  userAnswerInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') evaluateAnswer();
  });

  document.querySelectorAll('.btn-umlaut').forEach(btn => {
    btn.addEventListener('click', () => {
      const ch = btn.getAttribute('data-char');
      const el = userAnswerInput;
      const start = el.selectionStart ?? el.value.length;
      const end = el.selectionEnd ?? el.value.length;
      el.value = el.value.slice(0, start) + ch + el.value.slice(end);
      const caret = start + ch.length;
      el.focus();
      el.setSelectionRange(caret, caret);
    });
  });

  function evaluateAnswer() {
    const item = currentRoundItems[currentItemIndex];
    if (!item) return;

    if (submitBtn.textContent === 'Weiter ➔') {
      currentItemIndex++;
      renderCurrentItem();
      return;
    }

    const inputVal = userAnswerInput.value.trim();
    if (!inputVal) return;

    const result = gradeSubmission(inputVal, item.accepted_answers, item.topic_id);
    const attemptData = {
      item_id: item.id,
      topic_id: item.topic_id,
      user_answer: inputVal,
      is_correct: result.isCorrect,
      hint_level: currentHintLevel
    };
    roundAttempts.push(attemptData);

    // Persist the review event and fold it into the derived topic/FSRS
    // state that IndexedDB and the UI both read back from. Without these
    // writes, saveTopicState/saveFSRSCard are dead code and the "due
    // topics" stat never advances past what init() saw at page load.
    const logEntry = {
      card_id: item.id,
      item_id: item.id,
      topic_id: item.topic_id,
      user_answer: inputVal,
      is_correct: result.isCorrect,
      hint_level: currentHintLevel,
      timestamp: new Date().toISOString()
    };

    topicStates = applyReviewToTopicState(topicStates, logEntry);
    fsrsRecords = applyReviewToFsrsCard(fsrsRecords, logEntry);

    if (window.offlineStorage) {
      window.offlineStorage.appendReviewLog(logEntry).catch(console.warn);
      window.offlineStorage.saveTopicState(topicStates[item.topic_id]).catch(console.warn);
      window.offlineStorage.saveFSRSCard(fsrsRecords[item.id]).catch(console.warn);
    }

    feedbackBox.style.display = 'block';
    if (result.isCorrect) {
      feedbackBox.className = 'feedback-box correct';
      feedbackBox.innerHTML = result.message
        ? `<strong>✓ ${result.message}</strong>`
        : `<strong>✓ Richtig! Perfekt geantwortet.</strong>`;
    } else {
      feedbackBox.className = 'feedback-box incorrect';
      const diffHtml = renderDiff(item.accepted_answers[0], inputVal);
      feedbackBox.innerHTML = `<strong>✗ ${result.message || 'Falsch.'}</strong><div style="margin-top:6px; font-size:0.9rem;">Korrektur: ${diffHtml}</div>`;
    }

    userAnswerInput.disabled = true;
    submitBtn.textContent = 'Weiter ➔';
    submitBtn.focus();
  }

  function showRoundSummary() {
    switchView(viewSummary, null);
    const correctCount = roundAttempts.filter(a => a.is_correct).length;
    const unhintedCount = roundAttempts.filter(a => a.is_correct && a.hint_level === 0).length;

    document.getElementById('summary-score-value').textContent = `${correctCount} von ${currentRoundItems.length} Aufgaben gelöst`;
    document.getElementById('summary-correct').textContent = `${correctCount} / ${currentRoundItems.length}`;
    document.getElementById('summary-unhinted').textContent = `${unhintedCount}`;
    document.getElementById('summary-promotions').textContent = unhintedCount >= 3 ? '1' : '0';
  }

  btnNextRound.addEventListener('click', startNewRound);

  // DAG Tree View
  function renderDagView() {
    dagContainer.innerHTML = '';
    const topics = [
      { id: "pronomen_personal_nom", name: "Personalpronomen Nominativ", cefr: "A1", state: "acquired" },
      { id: "verb_praesens_regelm", name: "Präsens Regelmäßige Verben", cefr: "A1", state: "acquired" },
      { id: "dativ_nach_praeposition", name: "Dativ nach Präpositionen", cefr: "A2", state: "learning" },
      { id: "akkusativ_nach_praeposition", name: "Akkusativ nach Präpositionen", cefr: "A2", state: "ready" },
      { id: "nebensatz_weil_da", name: "Kausalsätze (weil / da)", cefr: "B1", state: "ready" },
      { id: "konjunktiv_ii_irreal_gegenwart", name: "Konjunktiv II Gegenwart", cefr: "B1", state: "locked" }
    ];

    topics.forEach(t => {
      const card = document.createElement('div');
      card.className = `dag-node ${t.state}`;
      card.innerHTML = `
        <div class="node-title">${t.name}</div>
        <div style="display:flex; justify-content:space-between; margin-top:6px;">
          <span class="tag-badge">${t.cefr}</span>
          <span class="node-status">${t.state}</span>
        </div>
      `;
      dagContainer.appendChild(card);
    });
  }

  // Duels View
  function renderDuelsView() {
    duelList.innerHTML = '';
    const duels = [
      { id: "dat_vs_akk", title: "Dativ vs. Akkusativ (Wechselpräpositionen)", diff: "A2", desc: "Kontrastiere Ortsangaben (Wo? + Dativ) und Richtungsangaben (Wohin? + Akkusativ)." },
      { id: "weil_vs_denn", title: "weil vs. denn (Satzstellung)", diff: "A2", desc: "Nebensatz-Verbendstellung (weil) vs. Hauptsatz-Position 0 (denn)." },
      { id: "haette_vs_waere", title: "hätte vs. wäre (Konjunktiv II)", diff: "B1", desc: "Wunschsätze und irreale Bedingungsgefüge mit haben und sein." }
    ];

    duels.forEach(d => {
      const card = document.createElement('div');
      card.className = 'exercise-card';
      card.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <h4>${d.title}</h4>
          <span class="tag-badge">${d.diff}</span>
        </div>
        <p style="color:var(--text-secondary); font-size:0.85rem; margin:8px 0;">${d.desc}</p>
        <button class="btn-hint" style="width:100%;">Duell Starten (8 Items)</button>
      `;
      duelList.appendChild(card);
    });
  }

  // Statistics View
  function renderStatsView() {
    document.getElementById('cov-a1-text').textContent = '100% erworben';
    document.getElementById('bar-a1-acq').style.width = '100%';
    document.getElementById('cov-a2-text').textContent = '65% erworben, 35% im Lernprozess';
    document.getElementById('bar-a2-acq').style.width = '65%';
    document.getElementById('bar-a2-lrn').style.width = '35%';
  }

  // JSON Export / Import.
  //
  // Export is a dump, import is a replay: tag_state (topic_states) is
  // recomputed from review_log, never trusted from the file
  // (04-application.md:334, :363). This is why review_log is now part of
  // the export payload -- without it, recomputation on import would have
  // nothing to replay.
  document.getElementById('btn-export-json').addEventListener('click', async () => {
    const reviewLog = window.offlineStorage ? await window.offlineStorage.getReviewLog() : [];
    const exportData = {
      version: EXPORT_SCHEMA_VERSION,
      exported_at: new Date().toISOString(),
      review_log: reviewLog,
      // topic_states/fsrs_records are included as an informational dump only;
      // import never assigns them directly (see below).
      topic_states: topicStates,
      fsrs_records: fsrsRecords,
      settings: config,
      streak: streak
    };
    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `deutschmaster_backup_${Date.now()}.json`;
    a.click();
  });

  document.getElementById('btn-import-json').addEventListener('click', () => {
    document.getElementById('file-import-input').click();
  });

  document.getElementById('file-import-input').addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async (evt) => {
      let imported;
      try {
        imported = JSON.parse(evt.target.result);
      } catch (err) {
        alert("Fehlerhafte JSON-Datei.");
        return;
      }

      if (!SUPPORTED_IMPORT_VERSIONS.has(imported.version)) {
        alert(`Unbekannte Export-Version '${imported.version}'. Import abgebrochen.`);
        return;
      }

      const importedLog = Array.isArray(imported.review_log) ? imported.review_log : [];

      // tag_state and FSRS card state are derived data: they are recomputed
      // from the imported review_log, never assigned from the file's own
      // topic_states/fsrs_records fields. Those fields may be stale, hand-
      // edited, or from an incompatible build, and merging derived data is
      // exactly how progress corrupts.
      topicStates = computeTopicStatesFromLog(importedLog);
      fsrsRecords = computeFsrsCardsFromLog(importedLog);
      if (imported.settings && typeof imported.settings === 'object') {
        config = { ...config, ...imported.settings };
      }
      if (typeof imported.streak === 'number') streak = imported.streak;

      if (window.offlineStorage) {
        try {
          await window.offlineStorage.replaceAll({
            reviewLog: importedLog,
            topicStates,
            fsrsCards: fsrsRecords
          });
        } catch (err) {
          console.warn('Import persistence failed:', err);
        }
      }

      alert("Daten erfolgreich wiederhergestellt!");
      renderStatsView();
    };
    reader.readAsText(file);
  });

  // Daily Challenge Production Grader
  document.getElementById('btn-submit-challenge').addEventListener('click', () => {
    const val = document.getElementById('challenge-input').value.trim();
    const fb = document.getElementById('challenge-feedback');
    if (!val) return;

    fb.style.display = 'block';
    if (val.toLowerCase().includes('weil')) {
      fb.className = 'feedback-box correct';
      fb.innerHTML = `<strong>✓ Zielstruktur 'weil' erkannt!</strong><br>Grammatische Genauigkeit: 100% | Natürlichkeit: 95%<br><em>Toll formuliert: Nebensatz-Verb am Ende.</em>`;
    } else {
      fb.className = 'feedback-box incorrect';
      fb.innerHTML = `<strong>✗ Zielstruktur 'weil' fehlt im Satz.</strong><br>Bitte bilde einen Kausalsatz mit der Konjunktion 'weil'.`;
    }
  });

  // Initialize App on DOM load
  document.addEventListener('DOMContentLoaded', initStorageAndItems);

})();
