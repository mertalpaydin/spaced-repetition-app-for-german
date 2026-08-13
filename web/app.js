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
      accepted_answers: ["Ich", "ich"],
      distractors: [{text: "Du"}, {text: "Er"}, {text: "Wir"}],
      rule_hint: "Subjekt in der 1. Person Singular ist 'ich'.",
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

  // State
  let bankItems = [];
  let currentRoundItems = [];
  let currentItemIndex = 0;
  let currentHintLevel = 0;
  let roundAttempts = [];
  let streak = 0;

  // Local Storage State
  let topicStates = JSON.parse(localStorage.getItem('dm_topic_states') || '{}');
  let fsrsRecords = JSON.parse(localStorage.getItem('dm_fsrs_records') || '{}');

  // DOM Elements
  const viewPractice = document.getElementById('view-practice');
  const viewSummary = document.getElementById('view-summary');
  const viewDag = document.getElementById('view-dag');
  const viewStats = document.getElementById('view-stats');

  const navPractice = document.getElementById('nav-practice-btn');
  const navDag = document.getElementById('nav-dag-btn');
  const navStats = document.getElementById('nav-stats-btn');

  const promptEl = document.getElementById('exercise-prompt');
  const cuedArea = document.getElementById('cued-area');
  const cuedWord = document.getElementById('cued-word');
  const userInput = document.getElementById('user-answer-input');
  const submitBtn = document.getElementById('submit-btn');
  const optionsArea = document.getElementById('options-area');
  const textInputArea = document.getElementById('text-input-area');
  const requestHintBtn = document.getElementById('request-hint-btn');
  const hintBox = document.getElementById('hint-box');
  const hintStatusText = document.getElementById('hint-status-text');
  const feedbackBox = document.getElementById('feedback-box');

  const roundProgressText = document.getElementById('stat-round-progress');
  const sessionProgressFill = document.getElementById('session-progress-fill');
  const cardTagBadge = document.getElementById('card-tag-badge');
  const cardCefrBadge = document.getElementById('card-cefr-badge');
  const cardDiffBadge = document.getElementById('card-diff-badge');
  const streakText = document.getElementById('stat-streak');

  const btnNextRound = document.getElementById('btn-next-round');

  // Navigation Handler
  function switchView(viewName) {
    [viewPractice, viewSummary, viewDag, viewStats].forEach(v => v.classList.remove('active'));
    [navPractice, navDag, navStats].forEach(n => n.classList.remove('active'));

    if (viewName === 'practice') {
      viewPractice.classList.add('active');
      navPractice.classList.add('active');
    } else if (viewName === 'summary') {
      viewSummary.classList.add('active');
    } else if (viewName === 'dag') {
      viewDag.classList.add('active');
      navDag.classList.add('active');
      renderDagTree();
    } else if (viewName === 'stats') {
      viewStats.classList.add('active');
      navStats.classList.add('active');
      renderStats();
    }
  }

  navPractice.addEventListener('click', () => switchView('practice'));
  navDag.addEventListener('click', () => switchView('dag'));
  navStats.addEventListener('click', () => switchView('stats'));

  // Load Bank Items
  async function loadBankData() {
    try {
      const res = await fetch('./data/all_items.json');
      if (res.ok) {
        bankItems = await res.json();
      } else {
        bankItems = DEFAULT_SEED_ITEMS;
      }
    } catch (e) {
      bankItems = DEFAULT_SEED_ITEMS;
    }
    startNewRound();
  }

  // Start Round
  function startNewRound() {
    currentRoundItems = bankItems.slice(0, 6);
    currentItemIndex = 0;
    roundAttempts = [];
    currentHintLevel = 0;
    renderCurrentItem();
    switchView('practice');
  }

  btnNextRound.addEventListener('click', startNewRound);

  // Render Exercise Item
  function renderCurrentItem() {
    if (currentItemIndex >= currentRoundItems.length) {
      showRoundSummary();
      return;
    }

    function setupEventListeners() {
      // Soft keys for German umlauts
      document.querySelectorAll('.btn-umlaut').forEach(btn => {
        btn.addEventListener('click', () => {
          const char = btn.getAttribute('data-char');
          if (userInput && char) {
            userInput.value += char;
            userInput.focus();
          }
        });
      });

      if (submitBtn) {
        submitBtn.addEventListener('click', handleAnswerSubmission);
      }
      if (userInput) {
        userInput.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            handleAnswerSubmission();
          }
        });
      }

      if (requestHintBtn) {
        requestHintBtn.addEventListener('click', handleRequestHint);
      }

      // Global keyboard shortcuts
      document.addEventListener('keydown', (e) => {
        if (e.key === 'h' || e.key === 'H') {
          if (document.activeElement !== userInput) {
            e.preventDefault();
            handleRequestHint();
          }
        }
      });
    }

    function renderDiff(userStr, targetStr) {
      return `<span style="color: var(--error); text-decoration: line-through;">${userStr}</span> &rarr; <span style="color: var(--success); font-weight: bold;">${targetStr}</span>`;
    }

    const item = currentRoundItems[currentItemIndex];
    currentHintLevel = 0;

    // Reset UI
    feedbackBox.className = 'feedback-box';
    feedbackBox.style.display = 'none';
    hintBox.classList.remove('visible');
    hintBox.textContent = '';
    hintStatusText.textContent = 'Hinweis-Stufe: 0 / 4';
    optionsArea.style.display = 'none';
    textInputArea.style.display = 'flex';
    userInput.value = '';
    userInput.disabled = false;
    userInput.focus();
    submitBtn.textContent = 'Prüfen';
    submitBtn.onclick = handleAnswerSubmission;

    // Update Badges & Progress
    roundProgressText.textContent = `${currentItemIndex + 1} / ${currentRoundItems.length}`;
    sessionProgressFill.style.width = `${((currentItemIndex) / currentRoundItems.length) * 100}%`;
    cardTagBadge.textContent = item.topic_id.replace(/_/g, ' ');
    cardCefrBadge.textContent = item.cefr;
    cardDiffBadge.textContent = `Stufe ${item.difficulty}`;
    streakText.textContent = `${streak} 🔥`;

    // Render Prompt
    const formattedPrompt = item.prompt.replace('___', '<span class="gap-blank">___</span>');
    promptEl.innerHTML = formattedPrompt;

    if (item.cue) {
      cuedArea.style.display = 'block';
      cuedWord.textContent = item.cue;
    } else {
      cuedArea.style.display = 'none';
    }
  }

  // Hint Degradation Step
  function handleRequestHint() {
    if (currentHintLevel >= 4) return;
    currentHintLevel++;

    const item = currentRoundItems[currentItemIndex];
    hintStatusText.textContent = `Hinweis-Stufe: ${currentHintLevel} / 4`;

    if (currentHintLevel === 1) {
      // Level 1: Length / Letter Shape Cue
      const ans = item.accepted_answers[0];
      hintBox.textContent = `Wortlänge: ${ans.length} Buchstaben. Anfangsbuchstabe: '${ans[0]}'`;
      hintBox.classList.add('visible');
    } else if (currentHintLevel === 2) {
      // Level 2: Multiple Choice Options Grid
      textInputArea.style.display = 'none';
      optionsArea.style.display = 'grid';
      optionsArea.innerHTML = '';

      const allOptions = [
        item.accepted_answers[0],
        ...item.distractors.map(d => typeof d === 'string' ? d : d.text)
      ].sort(() => Math.random() - 0.5);

      allOptions.forEach((opt) => {
        const btn = document.createElement('button');
        btn.className = 'option-btn';
        btn.textContent = opt;
        btn.onclick = () => {
          userInput.value = opt;
          handleAnswerSubmission();
        };
        optionsArea.appendChild(btn);
      });
      hintBox.textContent = `Wähle aus den folgenden 4 Optionen:`;
      hintBox.classList.add('visible');
    } else if (currentHintLevel === 3) {
      // Level 3: Grammatical Rule Stated
      hintBox.textContent = item.rule_hint || "Beachte die grammatikalische Regel für diesen Kasus/Modus.";
      hintBox.classList.add('visible');
    } else if (currentHintLevel === 4) {
      // Level 4: Full Answer Revealed
      hintBox.textContent = `Lösung: ${item.accepted_answers.join(' / ')}`;
      hintBox.classList.add('visible');
      userInput.value = item.accepted_answers[0];
    }
  }

  requestHintBtn.addEventListener('click', handleRequestHint);

  // Keyboard Shortcuts (Enter to submit, H for Hint)
  window.addEventListener('keydown', (e) => {
    if (viewPractice.classList.contains('active')) {
      if (e.key === 'Enter' && !feedbackBox.style.display.includes('block')) {
        handleAnswerSubmission();
      } else if (e.key === 'Enter' && feedbackBox.style.display === 'block') {
        currentItemIndex++;
        renderCurrentItem();
      } else if ((e.key === 'h' || e.key === 'H') && document.activeElement !== userInput) {
        handleRequestHint();
      }
    }
  });

  // Handle Answer Submission
  function handleAnswerSubmission() {
    const item = currentRoundItems[currentItemIndex];
    const userAns = userInput.value.trim();
    if (!userAns) return;

    const isCorrect = item.accepted_answers.some(a => a.toLowerCase() === userAns.toLowerCase());
    const isUnhinted = currentHintLevel === 0 && isCorrect;

    if (isCorrect) {
      streak++;
      feedbackBox.className = 'feedback-box success';
      feedbackBox.textContent = `Richtig! 🎉 (${item.accepted_answers.join(' / ')})`;
    } else {
      streak = 0;
      feedbackBox.className = 'feedback-box error';
      feedbackBox.textContent = `Nicht ganz richtig. Richtige Antwort: ${item.accepted_answers.join(' / ')}`;
    }
    feedbackBox.style.display = 'block';

    // Record attempt
    roundAttempts.push({
      item_id: item.id,
      topic_id: item.topic_id,
      is_correct: isCorrect,
      hint_level: currentHintLevel,
      is_unhinted: isUnhinted
    });

    // Update Topic State in LocalStorage
    updateTopicState(item.topic_id, isUnhinted, item.facet);

    submitBtn.textContent = 'Weiter (Enter)';
    submitBtn.onclick = () => {
      currentItemIndex++;
      renderCurrentItem();
    };
  }

  function updateTopicState(topicId, isUnhinted, facet) {
    if (!topicStates[topicId]) {
      topicStates[topicId] = {
        state: 'learning',
        consecutive_passes: 0,
        facets: []
      };
    }
    const s = topicStates[topicId];
    if (isUnhinted) {
      s.consecutive_passes++;
      if (facet && !s.facets.includes(facet)) s.facets.push(facet);
      if (s.consecutive_passes >= 3 && s.facets.length >= 2) {
        s.state = 'acquired';
      }
    } else {
      s.consecutive_passes = 0;
    }
    localStorage.setItem('dm_topic_states', JSON.stringify(topicStates));
  }

  // Show Round Summary
  function showRoundSummary() {
    sessionProgressFill.style.width = '100%';
    const total = roundAttempts.length;
    const correct = roundAttempts.filter(a => a.is_correct).length;
    const unhinted = roundAttempts.filter(a => a.is_unhinted).length;
    const scorePct = Math.round((correct / (total || 1)) * 100);

    document.getElementById('summary-score-value').textContent = `${scorePct}%`;
    document.getElementById('summary-correct').textContent = `${correct} / ${total}`;
    document.getElementById('summary-unhinted').textContent = `${unhinted}`;

    switchView('summary');
  }

  // Render DAG Tree
  function renderDagTree() {
    const container = document.getElementById('dag-tree-container');
    container.innerHTML = '';

    const topics = [
      {id: "pronomen_personal_nom", name: "Personalpronomen Nominativ", cefr: "A1", defaultState: "ready"},
      {id: "verb_praesens_regelm", name: "Verben Präsens Regelmäßig", cefr: "A1", defaultState: "ready"},
      {id: "dativ_nach_praeposition", name: "Dativ nach Präpositionen", cefr: "A2", defaultState: "locked"},
      {id: "akkusativ_nach_praeposition", name: "Akkusativ nach Präpositionen", cefr: "A2", defaultState: "locked"},
      {id: "nebensatz_weil_da", name: "Kausalsätze (weil / da)", cefr: "B1", defaultState: "locked"},
      {id: "konjunktiv_ii_irreal_gegenwart", name: "Konjunktiv II Gegenwart", cefr: "B1", defaultState: "locked"}
    ];

    let acquiredCount = 0;
    topics.forEach(t => {
      const saved = topicStates[t.id];
      const state = saved ? saved.state : t.defaultState;
      if (state === 'acquired') acquiredCount++;

      const card = document.createElement('div');
      card.className = `dag-node ${state}`;
      card.innerHTML = `
        <span class="tag-badge">${t.cefr}</span>
        <div class="node-title">${t.name}</div>
        <div class="node-status">Status: ${state}</div>
      `;
      container.appendChild(card);
    });

    document.getElementById('dag-acquired-counter').textContent = `${acquiredCount} / ${topics.length} Erworben`;
  }

  // Render Stats
  function renderStats() {
    const totalReviews = roundAttempts.length || 6;
    document.getElementById('stats-total-reviews').textContent = totalReviews;
    document.getElementById('stats-accuracy-overall').textContent = '92%';
    document.getElementById('stats-forecast-load').textContent = '14';
  }

  // Service Worker Registration
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('./sw.js').catch(err => {
        console.log('Service Worker registration skipped:', err);
      });
    });
  }

  // Initialize
  loadBankData();
})();
