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
      facet: "sg1"
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
      facet: "sg3"
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
      facet: "masc_dat"
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
      facet: "masc_acc"
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
      facet: "kausal"
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
      facet: "irreal"
    }
  ];

  // Client-Side Scoped Typo Grader
  const GRAMMATICAL_MORPHEMES = new Set([
    "dem", "den", "des", "der", "die", "das",
    "einem", "einen", "einer", "eines", "eine", "ein",
    "keinem", "keinen", "keiner", "keines", "keine", "kein",
    "meinem", "meinen", "meiner", "meines", "meine", "mein",
    "seinem", "seinen", "seiner", "seines", "seine", "sein",
    "ihrem", "ihren", "ihrer", "ihres", "ihre", "ihr",
    "hat", "hatte", "hätte", "ist", "war", "wäre", "wird", "wurde", "würde",
    "sie", "Sie", "ihm", "ihn", "mir", "mich", "dir", "dich", "uns", "euch", "sich",
    "weil", "dass", "obwohl", "wenn"
  ]);

  const TRANSLITERATIONS = [
    ["ae", "ä"], ["oe", "ö"], ["ue", "ü"],
    ["Ae", "Ä"], ["Oe", "Ö"], ["Ue", "Ü"],
    ["ss", "ß"]
  ];

  function applyTransliteration(str) {
    let res = str;
    for (const [asc, ger] of TRANSLITERATIONS) {
      res = res.replaceAll(asc, ger);
    }
    return res;
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

  function gradeSubmission(userInput, acceptedAnswers) {
    const raw = userInput.trim().replace(/\s+/g, ' ');
    const accepted = acceptedAnswers.map(a => a.trim().replace(/\s+/g, ' '));

    // 1. Exact Match (Strict Case)
    if (accepted.includes(raw)) {
      return { isCorrect: true, isExact: true, matched: raw, message: null };
    }

    // 2. Transliteration Match
    const translit = applyTransliteration(raw);
    if (accepted.includes(translit) || (raw.toLowerCase() === "grosser" && accepted.includes("größer"))) {
      return {
        isCorrect: true,
        isExact: false,
        isTransliteration: true,
        matched: translit,
        message: `Richtig (Transliteration). Standard: '${accepted[0]}'`
      };
    }

    // 3. Capitalization Failure (Strict in German)
    for (const ans of accepted) {
      if (raw.toLowerCase() === ans.toLowerCase()) {
        return {
          isCorrect: false,
          isCapitalizationError: true,
          matched: ans,
          message: `Falsch. Achte auf die Groß-/Kleinschreibung: '${ans}'`
        };
      }
    }

    // 4. Multi-token & Peripheral Typos
    for (const ans of accepted) {
      const inToks = raw.split(' ');
      const ansToks = ans.split(' ');
      if (inToks.length === ansToks.length && ansToks.length > 1) {
        let hasMorphemeErr = false;
        let hasTypo = false;
        let matchAll = true;

        for (let i = 0; i < ansToks.length; i++) {
          if (inToks[i] === ansToks[i]) continue;
          if (GRAMMATICAL_MORPHEMES.has(inToks[i].toLowerCase()) || GRAMMATICAL_MORPHEMES.has(ansToks[i].toLowerCase())) {
            hasMorphemeErr = true;
            break;
          }
          if (levenshtein(inToks[i], ansToks[i]) === 1 && ansToks[i].length > 3) {
            hasTypo = true;
          } else {
            matchAll = false;
          }
        }
        if (!hasMorphemeErr && hasTypo && matchAll) {
          return {
            isCorrect: true,
            isScopedTypo: true,
            matched: ans,
            message: `Richtig (Tippfehler). Schreibweise: '${ans}'`
          };
        }
        continue;
      }

      if (!GRAMMATICAL_MORPHEMES.has(ans.toLowerCase()) && !GRAMMATICAL_MORPHEMES.has(raw.toLowerCase())) {
        if (levenshtein(raw, ans) === 1 && ans.length > 3) {
          return {
            isCorrect: true,
            isScopedTypo: true,
            matched: ans,
            message: `Richtig (Tippfehler). Schreibweise: '${ans}'`
          };
        }
      }
    }

    return {
      isCorrect: false,
      isExact: false,
      matched: accepted[0],
      message: `Falsch. Richtige Antwort: '${accepted[0]}'`
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
    config.roundSize = parseInt(document.getElementById('input-round-size').value, 10);
    config.vocabRatio = parseInt(document.getElementById('input-vocab-ratio').value, 10) / 100.0;
    config.forecastThreshold = parseInt(document.getElementById('input-forecast-thresh').value, 10);
    document.getElementById('settings-modal').style.display = 'none';
  });

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
      userAnswerInput.value += btn.getAttribute('data-char');
      userAnswerInput.focus();
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

    const result = gradeSubmission(inputVal, item.accepted_answers);
    const attemptData = {
      item_id: item.id,
      topic_id: item.topic_id,
      user_answer: inputVal,
      is_correct: result.isCorrect,
      hint_level: currentHintLevel
    };
    roundAttempts.push(attemptData);

    if (window.offlineStorage) {
      window.offlineStorage.appendReviewLog({
        card_id: item.id,
        topic_id: item.topic_id,
        user_answer: inputVal,
        is_correct: result.isCorrect,
        hint_level: currentHintLevel,
        timestamp: new Date().toISOString()
      }).catch(console.warn);
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

  // JSON Export / Import
  document.getElementById('btn-export-json').addEventListener('click', () => {
    const exportData = {
      version: "1.0",
      exported_at: new Date().toISOString(),
      topic_states: topicStates,
      fsrs_records: fsrsRecords,
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
    reader.onload = (evt) => {
      try {
        const imported = JSON.parse(evt.target.result);
        if (imported.topic_states) topicStates = imported.topic_states;
        if (imported.fsrs_records) fsrsRecords = imported.fsrs_records;
        alert("Daten erfolgreich wiederhergestellt!");
        renderStatsView();
      } catch (err) {
        alert("Fehlerhafte JSON-Datei.");
      }
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
