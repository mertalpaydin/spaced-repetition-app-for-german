"""The "generate" step: plain, natural German sentences at a CEFR level,
given a theme -- never a grammar topic, never a gap, never an answer.

Every live call goes through ``src.llm.client.GeminiLlmClient`` (CLAUDE.md
rule 4: every LLM call goes through that one wrapper). This module makes NO
live API calls itself and is never imported in a way that would trigger one:
``client_from_env`` only builds a client when a key is actually configured in
the environment, exactly mirroring ``src.generation.batch_client.
_build_llm_client_if_configured``, and ``build_sentence_generator`` falls
back to the deterministic offline ``MockSentenceGenerator`` whenever it
returns ``None`` -- the same offline-by-default pattern
``src.generation.pilot.run_pilot`` already uses for its batch client, so
tests and no-key local development never touch the network (CLAUDE.md
"Unit tests never touch the network").

## The pool problem this module also fixes

A pilot run that requested ~60 sentences from one theme produced 245 items
skewed hard toward whatever grammar the source text's register happened to
contain: 44 items of ``pronomen_personal_nom`` and 38 of
``verb_praesens_regelm`` because the source read like one first-person,
present-tense daily-routine narrative, against 1 each for
``kasus_genitiv_formen``, ``modalverben_praesens`` and
``adjektiv_komparativ_superlativ``. A single small request cannot fix that:
grammatical person and tense are properties of what the model was asked to
write, not something a bigger request of the same shape produces more of.

``generate_sentence_pool`` is the fix: it does not send one request for
``total`` sentences, it sends many smaller requests, each nudged toward a
different narrative perspective (which grammatical person the sentences
naturally use), a different time frame (which tense), a different register,
and a different sentence shape, cycling through a set of varied themes at
the same time. None of those hints names a grammar topic or an answer --
consistent with the architecture's own rule that the model is asked only for
natural language, per CLAUDE.md 2 and this module's own docstring above --
they nudge the model toward writing *as if* addressing a specific person or
narrating a specific time frame, the same way a textbook exercise author
would ("write about yesterday", "write to a friend"), not toward producing a
specific grammatical form on demand.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from src.contracts import CEFR, MODEL_GENERATE
from src.generation.blanking import carrier_validation
from src.llm.client import GeminiLlmClient

# ---------------------------------------------------------------------------
# TASK 1: the generation instruction, maintained in TWO languages.
#
# ``_INSTRUCTION_EN_REFERENCE_ONLY`` is documentation. It exists so the
# project owner (and any future reader) can read, in English, what this
# module actually asks the model for, without also having to read German.
# It is NEVER passed to ``build_prompt``'s output and NEVER sent to the API
# -- nothing in this module even imports it into a live code path. Its only
# consumer is a human, and the test suite (which checks it stays non-empty
# and stays a faithful structural mirror of the live text, not that a human
# has kept translating it -- see the module docstring's honesty note below).
#
# ``_INSTRUCTION_DE_LIVE`` is the OPERATIVE prompt text: the one actually
# sent to the model, in ``build_prompt``. It is German because the model is
# asked to produce German output, and an instruction written in the same
# language as the requested output keeps the whole prompt in one linguistic
# register instead of asking the model to switch languages the instant
# generation starts -- the same reason a German textbook's own exercise
# instructions are written in German, not translated instructions bolted on
# from an English original.
#
# The English text below is NOT a mechanical, sentence-by-sentence
# translation of the German (or vice versa): each is independently phrased,
# idiomatic prose in its own language that says the same four things --
# write natural correct German prose, no gaps/blanks/underscores/questions,
# no grammar terminology, respond with exactly this JSON shape and nothing
# else. Keeping them saying "the same thing" is a human, editorial
# responsibility this test suite cannot fully discharge: automated tests
# CANNOT verify semantic equivalence between two natural-language texts in
# two different languages, and this module makes no attempt to pretend
# otherwise. What the tests below actually assert, and no more, is: (1) both
# texts exist and are non-empty, (2) ``build_prompt`` uses the German text,
# never the English one, and (3) both texts independently satisfy the
# structural invariants that must hold in either language -- forbidding
# gaps/underscores, and demanding the same literal JSON shape. A future edit
# that changes what one language asks for, without updating the other to
# match, will NOT be caught by these tests; it can only be caught by a human
# reviewer reading both languages side by side.
_INSTRUCTION_EN_REFERENCE_ONLY = (
    "Write natural, grammatically correct German sentences for a language "
    "learner. Each sentence must be a complete, plain statement -- no gaps, "
    "no blanks, no underscores, no questions to the reader. Do not mention "
    "grammar, cases, articles, tenses, or any linguistic terminology "
    "anywhere in your output; just write ordinary German sentences a "
    'textbook would use as reading material. Use "dass" only when a '
    "clause reports what someone says, knows, believes, hopes, or is told; "
    "when the sentence expresses a reason, a purpose, or a condition, use "
    '"weil", "damit", or "wenn" instead, never "dass" for those. '
    "Respond with ONLY a JSON "
    'object of the exact shape {"sentences": ["...", "..."]} and nothing '
    "else -- no commentary, no markdown fence."
)

# THE LIVE PROMPT TEXT. ``build_prompt`` sends exactly this, not the English
# text above -- see ``test_build_prompt_uses_the_german_instruction_not_the_english_one``.
#
# The "dass" sentence (cycle 6, CLAUDE.md audit class C) is a direct
# generation-side repair, not just a carrier-validation backstop: the
# report's four bad items all used "dass" for a purpose, causal, or
# conditional clause ("..., dass Ihr Computer wieder sicher ist" where
# "damit" was meant), which is a different mistake from the "das"/"dass"
# confusion an earlier cycle already addressed. Naming the correct
# alternatives ("weil"/"damit"/"wenn") explicitly, rather than only telling
# the model what NOT to do, gives it something to write instead of leaving
# it to guess.
_INSTRUCTION_DE_LIVE = (
    "Schreibe natürliche, grammatisch korrekte deutsche Sätze für "
    "Deutschlernende. Jeder Satz muss eine vollständige, einfache Aussage "
    "sein: keine Lücken, keine Leerstellen, keine Unterstriche, keine Fragen "
    "an die Leserin oder den Leser. Nenne in der Ausgabe an keiner Stelle "
    "grammatische Fachbegriffe oder Regeln; schreibe einfach gewöhnliche "
    "deutsche Sätze, wie sie in einem Lehrbuch als Lesetext stehen könnten. "
    'Verwende "dass" nur, wenn ein Satzteil wiedergibt, was jemand sagt, '
    "weiß, glaubt, hofft oder mitgeteilt bekommt. Wenn ein Grund, ein Zweck "
    'oder eine Bedingung gemeint ist, benutze stattdessen "weil", "damit" '
    'oder "wenn" -- niemals "dass" dafür. '
    "Antworte ausschließlich mit einem JSON-Objekt der exakten Form "
    '{"sentences": ["...", "..."]} und mit nichts sonst -- kein Kommentar, '
    "kein Markdown-Codeblock."
)


# ---------------------------------------------------------------------------
# TASK 2: few-shot examples.
#
# ``src.generation.prompt_builder.PromptBuilder`` (the older, still-live
# item-generation path) primes its prompt with ``gold_few_shot_examples``
# drawn from a topic's spec sheet; this "generate, then blank" path lost that
# entirely -- ``build_prompt`` had zero German exemplars for the model to
# anchor its output on. These six are hand-written (not model output,
# deliberately re-checked by a human against the rules they demonstrate) and
# chosen for two specific reasons, not as a token gesture:
#
# 1. At least one directly repairs the exact structure that produced the
#    "kauft ich" defect this whole task exists to fix: a fronted adverbial
#    forces V2 word order (finite verb before the subject), and the model
#    needs a worked example of getting subject-verb agreement right across
#    that inversion, not just an abstract instruction to "be grammatical".
#    Sentence 1 below is that exact carrier shape, corrected, with a
#    THIRD-person subject (not first) so the model sees the inverted-order
#    pattern with the subject the buggy sentence actually needed.
# 2. Separable verbs were also implicated (the buggy sentence's own verb,
#    "einkaufen", splits into "kauft ... ein"): several examples below use a
#    separable verb ("einkaufen", "aufstehen", "einladen", "einschlafen",
#    "aufräumen"), including one in a subordinate clause, where the prefix
#    stays attached to the verb instead of splitting -- a second common
#    source of error this set gives the model a worked example against.
#
# Deliberately varied across all six grammatical persons (not just
# first-person singular, which is what produced the pilot's original topic
# skew per the module docstring above) so the model has no single person to
# anchor on regardless of which example it happens to weight most heavily.
_FEW_SHOT_EXAMPLES_DE: tuple[str, ...] = (
    # 3rd person singular, fronted adverbial + V2 inversion, separable verb
    # ("einkaufen") -- the direct repair of the audited "kauft ich" defect,
    # same carrier shape, correct agreement.
    "Auf dem Weg kauft sie ein paar frische Brötchen ein.",
    # 1st person singular, fronted adverbial + V2 inversion, separable verb
    # ("aufstehen").
    "Morgens stehe ich meistens um sechs Uhr auf.",
    # 1st person plural, fronted adverbial + V2 inversion, separable verb
    # ("einladen").
    "Am Wochenende laden wir gern Freunde zum Essen ein.",
    # 2nd person singular informal, subordinate clause (separable verb
    # "einschlafen" stays attached inside "bevor ...").
    "Du liest jeden Abend ein spannendes Buch, bevor du einschläfst.",
    # 2nd person plural informal, fronted adverbial + V2 inversion, separable
    # verb ("aufräumen").
    "Später räumt ihr sicher noch die Küche auf.",
    # 3rd person plural, fronted adverbial + V2 inversion (verb before a
    # single plural subject).
    "Heute Abend kommen unsere Nachbarn zu Besuch.",
)


def build_prompt(
    cefr: CEFR,
    theme: str,
    count: int,
    *,
    person: str | None = None,
    tense: str | None = None,
    register: str | None = None,
    structure: str | None = None,
    construction: str | None = None,
) -> str:
    """The full generation prompt, built from the LIVE German instruction
    (``_INSTRUCTION_DE_LIVE``, task 1) plus the hand-written few-shot
    examples (``_FEW_SHOT_EXAMPLES_DE``, task 2): CEFR level and theme are
    the only grammar-adjacent-looking inputs when the five optional hints
    are left at their default of ``None``.

    The first four keyword-only hints are how ``generate_sentence_pool``
    steers person, tense, register, and sentence structure without ever
    naming a grammar topic (see the module docstring): each is a
    plain-language nudge ("write about yesterday", "address a close friend
    directly"), not a grammar instruction ("use the Perfekt", "use the
    Dativ").

    ``construction`` is the fifth, added for the same reason but a
    different job: the four hints above vary the SHAPE of ordinary,
    everyday prose, but a general-purpose sentence pool rarely contains a
    relative clause, a passive, or a Futur II construction no matter how
    much of it is generated (see the module docstring's "pool problem this
    module also fixes", and ``CONSTRUCTION_HINTS`` below). ``construction``
    asks for the construction the same indirect way: by describing the
    COMMUNICATIVE INTENT it expresses ("say what was done to something
    without naming who did it"), never by naming the grammar that intent
    happens to fall out as. Written in German, like the live instruction
    itself (unlike the other four hints, which stayed in the English this
    module already used for them before this parameter existed) -- see
    ``CONSTRUCTION_HINTS``'s own comment for why."""
    lines = [_INSTRUCTION_DE_LIVE, "", "Beispiele für richtige, natürliche Sätze:"]
    lines.extend(f"- {example}" for example in _FEW_SHOT_EXAMPLES_DE)
    lines += ["", f"CEFR level: {cefr}", f"Theme: {theme}"]
    if person is not None:
        lines.append(f"Perspective: {person}")
    if tense is not None:
        lines.append(f"Time frame: {tense}")
    if register is not None:
        lines.append(f"Register: {register}")
    if structure is not None:
        lines.append(f"Sentence shape: {structure}")
    if construction is not None:
        lines.append(f"Kommunikatives Ziel: {construction}")
    lines.append(f"Number of sentences: {count}")
    return "\n".join(lines) + "\n"


class SentenceGenerator(Protocol):
    def generate(
        self,
        cefr: CEFR,
        theme: str,
        count: int,
        *,
        person: str | None = None,
        tense: str | None = None,
        register: str | None = None,
        structure: str | None = None,
        construction: str | None = None,
    ) -> list[str]: ...


def _strip_code_fence(text: str) -> str:
    """Gemini routinely wraps JSON output in a ```` ```json ... ``` ```` fence
    even when asked for raw JSON (confirmed against the live endpoint in
    ``src.generation.batch_client.GeminiBatchClient._parse_response``); strip
    it the same way before parsing."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```")
        stripped = stripped.removesuffix("```").strip()
    return stripped


def _parse_sentences(text: str) -> list[str]:
    """Parse the model's JSON response into a list of sentence strings.
    Malformed output (not JSON, no ``sentences`` list, a non-string entry)
    yields an empty list rather than being coerced -- the pipeline then
    simply has nothing to tag, not a crash."""
    try:
        payload = json.loads(_strip_code_fence(text))
    except json.JSONDecodeError:
        return []
    sentences = payload.get("sentences") if isinstance(payload, dict) else None
    if not isinstance(sentences, list):
        return []
    return [s.strip() for s in sentences if isinstance(s, str) and s.strip()]


class LiveSentenceGenerator:
    """Real generator: one ``GeminiLlmClient.generate`` call per request,
    through the single LLM entry point so cost accounting, caching, the
    spend ceiling, and the two-lane routing all apply exactly as they do to
    every other call in the app."""

    def __init__(self, llm_client: GeminiLlmClient) -> None:
        self._client = llm_client

    def generate(
        self,
        cefr: CEFR,
        theme: str,
        count: int,
        *,
        person: str | None = None,
        tense: str | None = None,
        register: str | None = None,
        structure: str | None = None,
        construction: str | None = None,
    ) -> list[str]:
        prompt = build_prompt(
            cefr,
            theme,
            count,
            person=person,
            tense=tense,
            register=register,
            structure=structure,
            construction=construction,
        )
        response_text = self._client.generate(
            prompt,
            model=MODEL_GENERATE,
            purpose="sentence_generation",
            is_user_content=False,
        )
        return _parse_sentences(response_text)


# Deterministic offline pool: real, hand-written German sentences (not
# randomly generated), used whenever no API key is configured -- CI, local
# dev, and every unit test. Reused nowhere near verbatim as the pytest test
# corpus (tests/test_blanking_selectors.py hand-checks its own, separately
# authored sentences) so a selector bug cannot hide behind the same fixture
# serving both roles.
#
# Deliberately varied across grammatical person and tense, organised by
# theme rather than as one narrative, per this module's own docstring on
# what the pilot's original ~60-sentence, single-narrative pool got wrong:
# the first block (the original 40) leans on case and comparison-triggering
# contexts (genitive prepositions, dative verbs, comparatives/superlatives);
# every themed block after it spreads across all six grammatical persons
# (ich/du/er-sie-es/wir/ihr/sie-Sie) and six tenses/moods (Präsens,
# Präteritum, Perfekt, Plusquamperfekt, a "würde"/Futur I construction, and
# Konjunktiv II), instead of nearly all being 1st-person-singular present
# tense. Every sentence in this pool passes
# ``carrier_validation.validate_carrier`` -- checked directly in
# tests/test_blanking_sentence_source.py, so a future edit that breaks one
# is caught immediately, not discovered downstream in a pilot report.
_MOCK_SENTENCE_POOL_BASE: tuple[str, ...] = (
    # -- General / case and comparison coverage (original 40) --------------
    "Der Hund läuft schnell durch den Park.",
    "Die Sonne scheint heute hell über der Stadt.",
    "Ein Mann steht vor der Tür und wartet.",
    "Keine Katze mag kaltes Wasser besonders gern.",
    "Mein Vater kocht heute Abend für die Familie.",
    "Ihre kleine Schwester lacht über den Witz.",
    "Ich sehe den Mann auf der anderen Straßenseite.",
    "Wir kaufen einen neuen Tisch für die Küche.",
    "Ich antworte dem Lehrer sehr höflich.",
    "Sie hilft einer alten Frau beim Einkaufen.",
    "Die Farbe der Blumen ist wirklich schön.",
    "Das Auto des Lehrers ist ziemlich neu.",
    "Ich lege das Buch auf den Tisch im Wohnzimmer.",
    "Er geht langsam in die Küche und kocht.",
    "Das Buch liegt ruhig auf dem Tisch.",
    "Er wartet auf den Bus an der Ecke.",
    "Wir fahren gemeinsam durch den langen Tunnel.",
    "Das Geschenk ist für meinen besten Vater.",
    "Ich fahre jeden Tag mit dem Bus zur Arbeit.",
    "Sie wohnt seit einem Jahr bei ihren Eltern.",
    "Während der Prüfung darf man nicht sprechen.",
    "Statt eines Autos kaufte er sich ein Fahrrad.",
    "Wegen des schlechten Wetters bleiben wir zuhause.",
    "Der alte Mann trinkt einen starken Kaffee.",
    "Ein alter Baum steht mitten im Garten.",
    "Kein netter Mensch würde so etwas wirklich tun.",
    "Frisches Brot schmeckt besonders gut am Morgen.",
    "Guter Wein kostet in dieser Stadt sehr viel.",
    "Wir trinken jeden Abend frische Milch.",
    "Er trinkt gern kalten Kaffee am Nachmittag.",
    "Er läuft heute schneller als sein Bruder.",
    "Sie ist intelligenter als ihr jüngerer Bruder.",
    "Der Zug ist schneller als der alte Bus.",
    "Er läuft am schnellsten von der ganzen Gruppe.",
    "Das ist heute das beste Restaurant der ganzen Stadt.",
    "Die Kinder spielen fröhlich im großen Garten.",
    "Der Zug fährt pünktlich um acht Uhr ab.",
    "Meine Mutter liest jeden Abend ein spannendes Buch.",
    "Der Kaffee ist heute Morgen leider kalt.",
    "Die Studenten lernen fleißig für die Prüfung.",
    # -- Alltag --------------------------------------------------------
    "Ich stehe jeden Morgen um sechs Uhr auf.",
    "Du frühstückst normalerweise sehr schnell.",
    "Er duscht kalt, weil das ihn wach macht.",
    "Wir putzen die Wohnung jeden Samstag zusammen.",
    "Ihr räumt eure Zimmer nie richtig auf.",
    "Sie planen ihren Tag immer sehr genau.",
    "Gestern habe ich den ganzen Tag verschlafen.",
    "Letzte Woche hattest du keine Zeit für Sport.",
    "Er hat gestern seinen Schlüssel verloren.",
    "Wir sind früh ins Bett gegangen.",
    "Ihr habt den Wecker wieder nicht gehört.",
    "Sie kochten früher jeden Abend gemeinsam.",
    # -- Reisen --------------------------------------------------------
    "Ich werde nächsten Sommer nach Japan fliegen.",
    "Du bist letztes Jahr durch Europa gereist.",
    "Er hat seinen Koffer am Flughafen vergessen.",
    "Wir waren letzten Winter in den Bergen.",
    "Ihr werdet bald eine lange Reise machen.",
    "Sie waren schon einmal in Australien gewesen, bevor sie nach Neuseeland flogen.",
    "Ich hätte gern eine Wegbeschreibung zum Bahnhof.",
    "Wir hätten die Reise gern verschoben, wenn wir mehr Zeit gehabt hätten.",
    "Er reist gern allein, weil er dabei viel nachdenkt.",
    "Die Familie plant jedes Jahr eine große Reise.",
    # -- Arbeit --------------------------------------------------------
    "Ich arbeite seit drei Jahren in einer kleinen Firma.",
    "Du wirst nächste Woche ein wichtiges Projekt übernehmen.",
    "Er hat gestern die ganze Präsentation vorbereitet.",
    "Wir hatten letzten Monat viel zu viel Arbeit.",
    "Ihr müsst den Bericht bis Freitag abgeben.",
    "Sie arbeiten meistens im Homeoffice.",
    "Meine Kollegin hat den Termin kurzfristig verschoben, weil ein Kunde abgesagt hat.",
    "Ich würde gern früher in Rente gehen, wenn es möglich wäre.",
    "Der Chef hatte das Meeting schon abgesagt, bevor wir ankamen.",
    "Ihr solltet eure Ideen offener im Team teilen.",
    # -- Familie --------------------------------------------------------
    "Ich rufe meine Eltern jeden Sonntag an.",
    "Du rufst deine Großeltern viel zu selten an.",
    "Er kümmert sich liebevoll um seinen kleinen Bruder.",
    "Wir feiern Weihnachten immer bei meiner Tante.",
    "Ihr streitet euch manchmal über Kleinigkeiten.",
    "Meine Schwester kocht jeden Sonntag für die ganze Familie.",
    "Meine Großmutter hat früher in einem kleinen Dorf gelebt.",
    "Ich hatte meine Cousine seit Jahren nicht mehr gesehen, bevor wir uns letzten Sommer trafen.",
    "Wir werden dieses Jahr alle gemeinsam Weihnachten feiern.",
    "Er wäre gern öfter bei seiner Familie, wenn die Arbeit es erlauben würde.",
    # -- Gesundheit --------------------------------------------------------
    "Ich fühle mich heute leider nicht besonders gut.",
    "Du solltest bei diesem Husten wirklich zum Arzt gehen.",
    "Er hat sich beim Fußball das Knie verletzt.",
    "Wir achten seit diesem Jahr viel mehr auf unsere Ernährung.",
    "Ihr geht abends viel zu spät ins Bett.",
    "Die Ärztin empfiehlt regelmäßige Bewegung an der frischen Luft.",
    "Er war letzten Winter sehr oft erkältet.",
    "Ich hätte den Termin beim Zahnarzt fast vergessen.",
    "Meine Nachbarin hatte sich schon erholt, bevor der Sommer begann.",
    "Ihr müsst nach der Operation noch zwei Wochen schonen.",
    # -- Essen --------------------------------------------------------
    "Ich esse am liebsten italienische Gerichte.",
    "Du isst viel zu selten frisches Gemüse.",
    "Er bestellt im Restaurant immer das gleiche Gericht.",
    "Wir backen jeden Sonntag frisches Brot.",
    "Ihr probiert gern neue Rezepte aus.",
    "Meine Freunde haben letzte Woche einen neuen Grieche entdeckt.",
    "Früher aßen wir viel öfter zusammen am Tisch.",
    "Ich werde am Wochenende einen Kuchen backen.",
    "Der Kellner hatte die Bestellung schon notiert, bevor wir uns entschieden hatten.",
    "Sie würden gern öfter auswärts essen, wenn es nicht so teuer wäre.",
    # -- Wetter --------------------------------------------------------
    "Es regnet heute ununterbrochen seit dem Morgen.",
    "Gestern hat es den ganzen Tag geschneit.",
    "Im letzten Sommer war es außergewöhnlich heiß.",
    "Morgen wird es angeblich wieder sonnig.",
    "Der Sturm hatte den Baum schon umgeworfen, bevor die Feuerwehr kam.",
    "Ich mag Gewitter, weil die Luft danach so klar ist.",
    "Ihr solltet bei diesem Sturm besser zuhause bleiben.",
    "Wir hätten den Ausflug verschoben, wenn wir den Regen vorhergesehen hätten.",
    # -- Freizeit --------------------------------------------------------
    "Ich lese abends am liebsten einen spannenden Roman.",
    "Du spielst seit Jahren in derselben Fußballmannschaft.",
    "Er malt in seiner Freizeit gern kleine Landschaften.",
    "Wir treffen uns freitags meistens im Park.",
    "Ihr singt zusammen in einem kleinen Chor.",
    "Meine Nachbarn spielen jeden Mittwoch Karten.",
    "Ich habe letztes Jahr das Klavierspielen wieder angefangen.",
    "Er hätte gern mehr Zeit für seine Hobbys, wenn die Arbeit weniger stressig wäre.",
    "Wir werden am Samstag zusammen ins Kino gehen.",
    # -- Umwelt --------------------------------------------------------
    "Ich trenne meinen Müll seit vielen Jahren konsequent.",
    "Du fährst inzwischen fast immer mit dem Fahrrad.",
    "Er spart bewusst Wasser beim Duschen.",
    "Wir pflanzen jedes Jahr neue Bäume im Garten.",
    "Ihr verzichtet neuerdings ganz auf Plastiktüten.",
    "Die Stadt hat letztes Jahr viele neue Radwege gebaut.",
    "Der Fluss war früher viel stärker verschmutzt, bevor die neuen Gesetze kamen.",
    "Wir würden gern mehr für die Umwelt tun, wenn es einfacher wäre.",
    # -- Technologie --------------------------------------------------------
    "Ich nutze mein Handy hauptsächlich für die Arbeit.",
    "Du hast dir letzten Monat einen neuen Laptop gekauft.",
    "Er programmiert seit seiner Kindheit kleine Spiele.",
    "Wir testen gerade eine neue Software im Büro.",
    "Ihr solltet eure Passwörter regelmäßig ändern.",
    "Meine Firma hatte das alte System schon ersetzt, bevor der Fehler auffiel.",
    "Ich werde mir bald ein neues Tablet kaufen.",
    "Die Entwicklerin würde die App gern schneller machen, wenn mehr Zeit bliebe.",
    # -- Einkaufen --------------------------------------------------------
    "Ich kaufe samstags immer auf dem Wochenmarkt ein.",
    "Du vergisst beim Einkaufen ständig deine eigene Tasche.",
    "Er sucht online meistens die günstigsten Angebote.",
    "Wir haben gestern einen neuen Sofa gekauft.",
    "Ihr bezahlt inzwischen fast nur noch mit der Karte.",
    "Meine Mutter hatte das Geschenk schon eingepackt, bevor ich es sah.",
    "Ich würde das Kleid sofort kaufen, wenn es günstiger wäre.",
    "Der Laden hat letzten Winter komplett neue Regale bekommen.",
    # -- Wohnen --------------------------------------------------------
    "Ich wohne seit zwei Jahren in dieser Stadt.",
    "Du streichst gerade dein altes Badezimmer.",
    "Er hat letztes Jahr eine neue Wohnung gefunden.",
    "Wir streichen am Wochenende das Wohnzimmer.",
    "Ihr zieht nächsten Monat in ein größeres Haus.",
    "Meine Nachbarn hatten schon eingepackt, bevor der Bus kam.",
    "Der Vermieter würde die Miete senken, wenn die Wohnung kleiner wäre.",
    # -- Schule --------------------------------------------------------
    "Ich lerne jeden Abend für die nächste Prüfung.",
    "Du hast die Hausaufgaben schon wieder vergessen.",
    "Er erklärt seinen Mitschülern gern schwierige Aufgaben.",
    "Wir schreiben nächste Woche einen wichtigen Test.",
    "Ihr müsst eure Referate bis Montag vorbereiten.",
    "Die Lehrerin hatte die Klassenarbeit schon korrigiert, bevor die Ferien begannen.",
    "Ich wäre gern besser in Mathematik, wenn ich mehr üben würde.",
    # -- Nachrichten --------------------------------------------------------
    "Ich lese morgens meistens die lokale Zeitung.",
    "Du informierst dich am liebsten über soziale Medien.",
    "Er hat gestern eine wichtige Nachricht verpasst.",
    "Wir diskutieren abends oft über aktuelle Themen.",
    "Ihr informiert euch selten über aktuelle Nachrichten.",
    "Der Sender hatte die Sendung schon beendet, bevor die Meldung kam.",
    # -- Sport --------------------------------------------------------
    "Ich laufe jeden Morgen eine kleine Runde durch den Park.",
    "Du trainierst inzwischen fast jeden Tag im Fitnessstudio.",
    "Er hat letztes Wochenende einen Marathon gelaufen.",
    "Wir spielen samstags regelmäßig Tennis.",
    "Ihr übt jede Woche neue Choreografien für den Tanzkurs.",
    "Die Mannschaft hatte das Spiel schon gewonnen, bevor die zweite Halbzeit begann.",
    "Ich würde gern öfter schwimmen gehen, wenn das Bad näher wäre.",
    # -- Freundschaft --------------------------------------------------------
    "Ich treffe meine beste Freundin fast jede Woche.",
    "Du hilfst deinen Freunden immer sehr gern.",
    "Er hat seinen alten Schulfreund zufällig wiedergetroffen.",
    "Wir organisieren jedes Jahr ein großes Klassentreffen.",
    "Ihr kennt euch inzwischen schon seit der Kindheit.",
    "Meine Freunde hatten die Party schon vorbereitet, bevor ich ankam.",
    "Ich würde ihm sofort verzeihen, wenn er sich entschuldigen würde.",
)


# -- Starved-construction coverage --------------------------------------------
#
# Hand-written, per-topic examples for the 16-of-49-topic pool-coverage gap
# (this module's own docstring, and ``CONSTRUCTION_HINTS`` above): a
# general-purpose pool of everyday sentences essentially never contains a
# relative clause, a passive, or a Futur II, so these are written
# deliberately, one topic at a time, instead of hoped for from more volume
# of ordinary prose.
#
# Keyed by TOPIC ID (not sent to the model -- this is the offline mock pool,
# read only by this process, never transmitted) so
# ``tests/test_blanking_sentence_source.py`` can assert, per topic, that
# ``selectors.SELECTORS[topic_id]`` actually finds a candidate on that
# topic's OWN examples -- not merely that "some sentence somewhere in the
# 500-sentence pool happens to work", which could hide a topic that still
# gets nothing. Every sentence below was run, individually, through
# ``carrier_validation.validate_carrier`` (accepted) and its own topic's
# selector (candidate found) before being added here; several hand-written
# first attempts were rejected by one or the other during that process and
# were rewritten, not weakened past the checks (CLAUDE.md 7) -- among the
# confirmed failure modes, worth recording so a future edit does not
# reintroduce them: a participle whose lemma is not in ``paradigms.
# TRANSITIVE_LEMMAS``/``KNOWN_PARTICIPLE_FORMS`` (e.g. "unterschrieben")
# never yields a passive/Zustandspassiv candidate even though the sentence
# itself is perfectly sound German -- "geschlossen" used to be a second,
# confirmed instance of exactly this (it lemmatises to "schließen", which
# did not match the ASCII "schliessen" key ``TRANSITIVE_LEMMAS`` carried at
# the time), but TODO.md 8.8 fixed that key -- see ``paradigms.py``'s own
# comment on it -- so "geschlossen" is no longer an example of this gap;
# a separable verb's fused zu-infinitive
# ("aufzustehen", one token, tag ``VVIZU``) never matches
# ``infinitiv_mit_zu``'s selector, which looks for a split ``PTKZU`` token
# immediately before a plain ``VVINF`` token; an inserted phrase between an
# extended attributive participle and its real governing determiner must
# itself contain NO determiner-tagged word ("von Experten", not "von der
# Firma") or ``_find_governing_declension_trigger`` finds the inner
# determiner instead of the real one and then fails the "at least one
# preposition in between" check; and ``de_core_news_sm`` reproducibly mistags
# the modal "muss" as a proper noun (``NE``) in a subject-muss-...-werden
# passive frame regardless of which noun precedes it, which
# ``carrier_validation`` then rejects as ``no_finite_verb`` -- confirmed on
# three different subjects, so every passiv_modalverben example below uses
# "kann", "soll", or "darf" instead.
#
# A subtler one, not a selector rejection but a PIPELINE-level one, found by
# running ``pipeline.blank_sentences`` (not just ``selectors.py`` in
# isolation) over these examples: ``selectors._select_passiv_praesens``
# fires on ANY "wird" immediately followed by a transitive participle, with
# no check for a further trailing aux-infinitive -- so a futur_ii sentence
# whose participle happens to be one of ``paradigms.TRANSITIVE_LEMMAS``
# ("geplant", "gelesen", "geschrieben", tried first) satisfies BOTH
# selectors on the same "wird" token, and ``pipeline._SPECIFICITY_OVERRIDES``
# has an entry for "futur_ii beats futur_i" but none for "futur_ii beats
# passiv_praesens" -- so three of the first four futur_ii examples tried
# lost their (prompt, answer) pair to passiv_praesens in
# ``_drop_cross_topic_duplicates`` and never became a futur_ii item at all,
# despite ``selectors.SELECTORS["futur_ii"]`` finding a candidate on every
# one of them. The fix applied below is on THIS module's side, not
# ``pipeline.py``'s (out of this file's ownership): every futur_ii example
# now uses a participle OUTSIDE ``TRANSITIVE_LEMMAS`` ("erledigt",
# "vorbereitet", "erklärt", "abgesagt"), which ``_select_passiv_praesens``'s
# own transitive-lemma gate then correctly excludes, confirmed by re-running
# ``pipeline.blank_sentences`` on the corrected four: zero cross-topic drops.
#
# ``passiv_unpersoenlich`` is the one topic named in the audit with NO entry
# in ``selectors.SELECTORS`` (see ``CONSTRUCTION_HINTS``'s own comment for
# why, and confirm directly with ``"passiv_unpersoenlich" not in
# selectors.SELECTORS``) -- its three examples below are carrier-sound
# (each uses the expletive "es" as subject, e.g. "Es wird hier abends oft
# getanzt.", which resolves the ``ep`` dependency carrier_validation needs;
# a genuinely SUBJECTLESS impersonal passive like "Hier wird getanzt." was
# tried first and rejected as ``no_subject_found`` -- honestly, still a
# correct German sentence, just one this module's own carrier checker
# cannot confirm) but included for pool realism only. No test in this
# suite claims a selector fires on them, because none exists to fire.
_STARVED_CONSTRUCTION_EXAMPLES: dict[str, tuple[str, ...]] = {
    "relativsatz_nom_akk": (
        "Ich kenne den Mann, der uns gestern geholfen hat.",
        "Wir suchen die Wohnung, die meine Schwester letztes Jahr gemietet hat.",
        "Er liest das Buch, das seine Kollegin ihm empfohlen hat.",
        "Ich mag die Kinder, die im Garten spielen.",
    ),
    "relativsatz_dativ": (
        "Sie sucht den Kollegen, dem sie gestern die Unterlagen geschickt hat.",
        "Er sucht den Freund, dem er das Buch geliehen hat.",
        "Das ist die Kollegin, der ich die Unterlagen geschickt habe.",
        "Wir kennen den Lehrer, dem die Schüler sehr vertrauen.",
    ),
    "relativsatz_genitiv": (
        "Das ist der Mann, dessen Auto letzte Woche gestohlen wurde.",
        "Ich kenne die Frau, deren Sohn an der Universität studiert.",
        "Wir besuchen die Familie, deren Haus neben dem Park steht.",
    ),
    "passiv_praesens": (
        "Das Fest wird jedes Jahr im Park organisiert.",
        "Das Essen wird gerade gekocht.",
        "Das neue Rathaus wird gerade renoviert.",
        "Das neue Museum wird gerade gebaut.",
    ),
    "passiv_praeteritum": (
        "Das alte Schloss wurde vor zwei Jahren renoviert.",
        "Der Brief wurde gestern Abend geschrieben.",
        "Die Firma wurde vor zehn Jahren gegründet.",
        "Das Auto wurde letzte Woche verkauft.",
    ),
    "passiv_modalverben": (
        "Das Fenster kann nicht mehr repariert werden.",
        "Die Suppe soll noch einmal gekocht werden.",
        "Der Brief darf heute noch geschrieben werden.",
        "Die Bücher können jederzeit gelesen werden.",
    ),
    "passiv_unpersoenlich": (
        "Es wird hier abends oft getanzt.",
        "Es wird bei uns am Wochenende gern gekocht.",
        "Es wurde auf der Party viel gelacht.",
    ),
    "zustandspassiv": (
        "Die Tür ist schon repariert.",
        "Der Brief ist bereits geschrieben.",
        "Das Haus ist inzwischen gebaut.",
        "Die Suppe ist schon gekocht.",
    ),
    "zustandspassiv_zeiten": (
        "Die Tür war gestern noch nicht repariert.",
        "Das Fenster war schon geöffnet, bevor wir ankamen.",
        "Der Laden ist inzwischen geöffnet gewesen.",
        "Das Auto war letzten Winter schon verkauft.",
    ),
    "infinitiv_mit_zu": (
        "Sie hofft, den neuen Job bald zu bekommen.",
        "Er versucht, das Problem endlich zu verstehen.",
        "Wir finden es schwierig, das Rezept genau zu befolgen.",
        "Ich plane, das Studium bald zu beginnen.",
    ),
    "infinitiv_um_zu": (
        "Sie lernt jeden Abend, um die Prüfung zu bestehen.",
        "Er steht früh auf, um den Bus nicht zu verpassen.",
        "Wir sparen Geld, um eine große Reise zu machen.",
        "Ich rufe an, um den Termin zu bestätigen.",
    ),
    "partizip_i_attributiv": (
        "Das spielende Kind lacht laut im Garten.",
        "Ein lachender Mann steht vor der Tür.",
        "Die schlafende Katze liegt auf dem Sofa.",
        "Der singende Chor probt jeden Mittwoch.",
    ),
    "partizip_ii_attributiv_erweitert": (
        "Das von Experten entwickelte Programm läuft sehr stabil.",
        "Das von Handwerkern renovierte Haus ist jetzt fertig.",
        "Die von Freiwilligen organisierte Aktion war ein Erfolg.",
        "Der von Studierenden gegründete Verein wächst schnell.",
    ),
    "kasus_genitiv_formen": (
        "Die Farbe des Autos gefällt mir sehr.",
        "Der Titel des Buches ist mir entfallen.",
        "Das Ergebnis der Prüfung war enttäuschend.",
        "Ich habe die Adresse meiner Tante vergessen.",
    ),
    "praepositionen_genitiv_gehoben": (
        "Anhand der Unterlagen konnte die Polizei den Fall klären.",
        "Infolge des schlechten Wetters wurde das Fest verschoben.",
        "Zugunsten der Opfer wurde eine Spendenaktion gestartet.",
        "Mittels eines neuen Verfahrens wurde das Problem gelöst.",
    ),
    "konjunktiv_ii_hoeflichkeit": (
        "Könnten Sie mir bitte kurz helfen?",
        "Hätten Sie einen Moment Zeit für mich?",
        "Würden Sie mir bitte das Fenster öffnen?",
    ),
    "futur_ii": (
        "Bis nächsten Montag wird er die Arbeit erledigt haben.",
        "Bis morgen Abend werden wir die Präsentation vorbereitet haben.",
        "Bis zum Sommer wird sie den Plan erklärt haben.",
        "Bis Freitag wird sie den Termin abgesagt haben.",
    ),
    # -- Appended by a later cycle: the three Nominative article topics
    # (CONSTRUCTION_HINTS's own comment above the three hints has the full
    # rationale). Each example was run, individually, through
    # ``carrier_validation.validate_carrier`` (accepted) and its own topic's
    # selector in ``selectors.SELECTORS`` (candidate found, and the
    # resulting item built cleanly through ``blanker.blank_candidate``)
    # before being added here, exactly like every other entry in this dict.
    "artikel_bestimmt_nom": (
        "Der Hund, den ich gestern gekauft habe, schläft im Garten.",
        "Die Frau, die neben mir wohnt, ist Ärztin.",
        "Das Kind, das im Garten spielt, lacht laut.",
        "Der größte Baum im Park ist über hundert Jahre alt.",
        "Der letzte Tag im Urlaub war wunderschön.",
    ),
    "artikel_unbestimmt_kein_nom": (
        "Wir kommen heute zu spät, weil kein Bus fährt.",
        "Weil keine Bäckerei heute geöffnet hat, kaufen wir das Brot woanders.",
        "Die Innenstadt bleibt leer, weil kein Geschäft heute geöffnet hat.",
    ),
    "artikel_possessiv_nom": (
        "Meine Großmutter, die ich jedes Wochenende besuche, wohnt in München.",
        "Dein Bruder, den du gestern angerufen hast, wohnt in Berlin.",
        "Sein Onkel, den ich letzten Sommer kennengelernt habe, lebt in Hamburg.",
        "Unsere Tante, die wir jedes Jahr besuchen, kocht sehr gut.",
    ),
    # -- Appended by a later cycle (feat/generate-then-blank): one entry per
    # remaining topic in ``CONSTRUCTION_HINTS`` (the 30 hints appended above),
    # covering every one of the 49 topics in ``selectors.SELECTORS`` that had
    # no construction hint before this cycle -- tenses, cases, prepositions,
    # pronouns, adjective declension, and finite verb conjugation. Most of
    # these are not "starved" in this dict's original, narrower sense (a
    # general-purpose sentence pool already contains plenty of ordinary
    # present-tense, Accusative, or personal-pronoun sentences -- see the
    # module docstring's own "pool problem" section) -- reused as the one
    # dict this module already has for "hand-written examples proven to
    # produce a candidate, keyed by topic", per
    # ``test_starved_construction_examples_cover_every_wired_construction_hint``'s
    # own requirement that every hinted topic have entries here. Each
    # example was run, individually, through
    # ``carrier_validation.validate_carrier`` (accepted) and its own topic's
    # selector in ``selectors.SELECTORS`` (candidate found) before being
    # added here, exactly like every other entry in this dict; three
    # confirmed failure modes worth recording so a future edit does not
    # reintroduce them: a 2nd-singular modal or vowel-change form ("du
    # kannst", "du darfst", "er isst") reproducibly mislemmatises to a
    # non-word ("kannstn", "darfstn") or the unreduced surface form under
    # this exact tagger, which fails ``verb_praesens_vokalwechsel``'s own
    # "does this cell actually show the change" check or excludes the token
    # from ``modalverben_praesens`` outright -- avoided below by using a
    # 1st/3rd-person subject for every modal example and a 2nd/3rd-singular
    # verb outside that mislemmatised set for every vowel-change example; an
    # infinitival complement clause after "werden"/"möchten" ("... das
    # Projekt beginnen.", "... einen Kaffee trinken.") is sometimes parsed
    # as its own finite-verb-headed clause with no complementizer, which
    # ``carrier_validation`` then rejects as ``missing_clause_connector`` --
    # avoided by keeping the infinitive simple and not the sentence's own
    # apparent second clause; and a fronted "Nachdem"/"Wenn" clause whose
    # subject/verb the parser mis-resolves reproducibly rejects as
    # ``subject_verb_disagreement`` for specific verb choices ("Nachdem sie
    # gegessen hatte, ging sie spazieren.", "Wenn sie mich gefragt hätte,
    # ...") even though the German itself is correct -- avoided below by
    # picking a different verb for the same construction rather than fighting
    # the tagger over a sentence this module cannot fix from its own side.
    "perfekt_sein": (
        "Meine Schwester ist gestern nach Berlin gefahren.",
        "Er ist heute Morgen sehr früh aufgewacht.",
        "Wir sind letzten Sommer nach Italien geflogen.",
    ),
    "perfekt_haben": (
        "Ich habe heute Morgen das Frühstück gekocht.",
        "Sie hat gestern Abend ihre Hausaufgaben gemacht.",
        "Wir haben heute den ganzen Nachmittag im Garten gearbeitet.",
    ),
    "praeteritum_vollverben": (
        "Der König lebte vor vielen hundert Jahren in einem großen Schloss.",
        "Die Kinder spielten den ganzen Nachmittag im Garten.",
        "Der Zug erreichte pünktlich um acht Uhr den Bahnhof.",
    ),
    "praeteritum_sein_haben_modal": (
        "Der Bericht war sehr ausführlich und genau.",
        "Die Familie hatte damals nur wenig Geld.",
        "Sie wollte damals unbedingt Ärztin werden.",
        "Er konnte als Kind sehr gut schwimmen.",
    ),
    "plusquamperfekt": (
        "Nachdem er die Tür geschlossen hatte, setzte er sich hin.",
        "Bevor der Zug ankam, hatten wir schon den Bahnsteig verlassen.",
        "Nachdem wir das Haus verkauft hatten, zogen wir nach Berlin.",
        "Nachdem sie den Brief gelesen hatte, rief sie ihre Schwester an.",
    ),
    "futur_i": (
        "Ich werde dir morgen um acht Uhr helfen.",
        "Sie wird nächste Woche ihre Prüfung ablegen.",
        "Ich werde dir am Montag meine Antwort geben.",
    ),
    "konjunktiv_ii_irreal_gegenwart": (
        "Wenn ich mehr Zeit hätte, würde ich öfter Sport machen.",
        "Wenn er mehr Geld hätte, würde er ein neues Auto kaufen.",
        "Wenn ich an deiner Stelle wäre, würde ich sofort absagen.",
    ),
    "konjunktiv_ii_vergangenheit": (
        "Wenn ich früher losgefahren wäre, hätte ich den Zug nicht verpasst.",
        "Wenn wir das Wetter gekannt hätten, hätten wir den Ausflug verschoben.",
        "Wenn er mehr geübt hätte, hätte er die Prüfung bestanden.",
    ),
    "adjektiv_komparativ_superlativ": (
        "Mein Bruder ist größer als ich.",
        "Dieses Haus ist teurer als das andere.",
        "Sie läuft am schnellsten von allen.",
    ),
    "adjektivdeklination_bestimmt": (
        "Ich kenne den kleinen Hund aus der Nachbarschaft.",
        "Die alte Frau wohnt neben uns.",
        "Er hat das rote Auto gestern gewaschen.",
    ),
    "adjektivdeklination_unbestimmt": (
        "Ich habe einen kleinen Hund gekauft.",
        "Sie trägt ein rotes Kleid heute Abend.",
        "Wir suchen eine neue Wohnung in der Stadt.",
    ),
    "adjektivdeklination_nullartikel": (
        "Frisches Brot schmeckt besonders gut.",
        "Kalte Milch trinke ich nicht so gern.",
        "Guter Kaffee kostet hier ziemlich viel.",
    ),
    "nomen_plural": (
        "Die Kinder spielen fröhlich im Garten.",
        "Meine Eltern besuchen uns jedes Wochenende.",
        "Die Bücher liegen auf dem Tisch.",
    ),
    "kasus_akkusativ_formen": (
        "Ich sehe den Mann auf der Straße.",
        "Wir besuchen einen alten Freund in München.",
        "Sie liest das Buch jeden Abend.",
    ),
    "kasus_dativ_formen": (
        "Ich danke meinem Kollegen für die Hilfe.",
        "Sie gratuliert ihrer Freundin zum Geburtstag.",
        "Wir schenken der Mutter einen Blumenstrauß.",
    ),
    "praepositionen_genitiv": (
        "Trotz des starken Regens gingen wir spazieren.",
        "Wegen des dichten Nebels fällt der Flug aus.",
        "Statt eines Kuchens backte sie eine Torte.",
    ),
    "praepositionen_akkusativ": (
        "Wir gehen heute Abend durch den Park.",
        "Dieses Geschenk ist für meinen besten Freund.",
        "Er kämpft immer für seine Familie.",
    ),
    "praepositionen_dativ": (
        "Ich fahre morgen zu meiner Tante.",
        "Wir fahren mit dem Zug zur Arbeit.",
        "Er kommt gerade aus dem Büro.",
    ),
    "akkusativ_nach_praeposition": (
        "Ich lege das Buch auf den Tisch.",
        "Er hängt das Bild an die Wand.",
        "Wir stellen die Kiste in den Keller.",
    ),
    "dativ_nach_praeposition": (
        "Das Buch liegt auf dem Tisch.",
        "Das Bild hängt an der Wand.",
        "Die Kiste steht in dem Keller.",
    ),
    # docs/audits/cycle-11-corpus-report.md, owner decision D2. These two
    # lists used to be built almost entirely from 3rd-person examples ("Er
    # kommt heute Abend zu Besuch.", "Wir besuchen sie am Wochenende."), and
    # every one of them is now correctly refused downstream: a 3rd-singular
    # subject blank accepts "er", "sie" and "es" alike because no German
    # verb form distinguishes them, and Accusative "sie" is spelled the same
    # as its own Nominative so it cannot be cued without handing over the
    # answer. An example sentence that cannot survive the pipeline is worse
    # than no example, since its whole job is to show the generator a shape
    # that works, so they are replaced with 1st and 2nd person and masculine
    # 3rd person, which are the cells these two topics can actually ship.
    "pronomen_personal_nom": (
        "Ich komme heute Abend zu Besuch.",
        "Du brauchst jeden Tag dein Fahrrad.",
        "Ich arbeite jeden Tag im Garten.",
    ),
    "pronomen_personal_akk": (
        "Ich sehe ihn jeden Morgen im Bus.",
        "Sie besucht mich am Wochenende.",
        "Er kennt mich schon seit der Schule.",
    ),
    "pronomen_personal_dat": (
        "Ich gebe ihm mein altes Fahrrad.",
        "Sie schenkt ihr einen schönen Blumenstrauß.",
        "Wir schreiben ihnen jede Woche einen Brief.",
    ),
    "modalverben_praesens": (
        "Ich will heute Abend noch arbeiten.",
        "Wir wollen nächstes Jahr nach Spanien reisen.",
        "Sie soll heute pünktlich kommen.",
    ),
    "verb_praesens_regelm": (
        "Wir üben freitags regelmäßig Klavier.",
        "Er kocht jeden Abend für seine Familie.",
        "Sie lernt jeden Abend fleißig für die Prüfung.",
    ),
    "verb_praesens_vokalwechsel": (
        "Du liest jeden Abend ein spannendes Buch.",
        "Sie fährt jeden Tag mit dem Bus zur Arbeit.",
        "Er sieht seinen Freund jeden Mittwoch.",
    ),
    "verb_sein_haben": (
        "Ich bin heute sehr müde.",
        "Er hat viel Zeit für seine Familie.",
        "Wir sind gerade in der Küche.",
    ),
    "verben_trennbar_praesens": (
        "Sie macht abends immer das Fenster zu.",
        "Er räumt jeden Samstag die Küche auf.",
        "Wir laden am Wochenende gern Freunde ein.",
    ),
    "verben_reflexiv_akk": (
        "Ich freue mich sehr über das Geschenk.",
        "Er ärgert sich über den Verkehr.",
        "Wir treffen uns jeden Freitag im Park.",
    ),
    "verben_reflexiv_dat": (
        "Ich kaufe mir ein neues Fahrrad.",
        "Er stellt sich das Ergebnis genau vor.",
        "Wir helfen uns gegenseitig bei den Hausaufgaben.",
    ),
}

_MOCK_SENTENCE_POOL: tuple[str, ...] = _MOCK_SENTENCE_POOL_BASE + tuple(
    sentence
    for topic_sentences in _STARVED_CONSTRUCTION_EXAMPLES.values()
    for sentence in topic_sentences
)


class MockSentenceGenerator:
    """Deterministic, offline sentence pool (CLAUDE.md 7: unit tests never
    touch the network), mirroring the pattern already used by
    ``src.generation.batch_client.MockBatchClient`` -- a fixed, inspectable
    substitute for the real model, never randomness, so a run with the same
    arguments is exactly reproducible.

    Unlike a single fixed slice of the pool, the starting position is a
    deterministic hash of every argument that identifies "which request this
    is" (``cefr``, ``theme``, and the five variety/construction hints) -- not
    the sentence content, which this offline mock cannot actually vary to
    match a hint the way a real model would. This is what lets
    ``generate_sentence_pool``'s many differently-themed batch calls surface
    different slices of the pool (242 sentences: the original 177-sentence
    everyday-prose pool plus ``_STARVED_CONSTRUCTION_EXAMPLES``'s
    construction-targeted additions) offline too, instead of the first
    ``count`` sentences over and over regardless of theme (which would make
    every batch collapse to the same handful of duplicates after
    deduplication, defeating the whole point of batching by theme)."""

    def generate(
        self,
        cefr: CEFR,
        theme: str,
        count: int,
        *,
        person: str | None = None,
        tense: str | None = None,
        register: str | None = None,
        structure: str | None = None,
        construction: str | None = None,
    ) -> list[str]:
        pool = _MOCK_SENTENCE_POOL
        if count <= 0 or not pool:
            return []
        key = "|".join(
            str(part) for part in (cefr, theme, person, tense, register, structure, construction)
        )
        offset = int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16) % len(pool)
        return [pool[(offset + i) % len(pool)] for i in range(count)]


def client_from_env() -> GeminiLlmClient | None:
    """Build a real ``GeminiLlmClient`` only when a lane key is actually
    configured in the environment; ``None`` otherwise. Mirrors
    ``src.generation.batch_client._build_llm_client_if_configured`` exactly
    -- kept as its own small copy here rather than importing that private
    helper, so this module stays self-contained within the new package.

    Built with ``forbid_batch=True`` and ``forbid_paid_lane=False``: this is
    a pilot script (``scripts/step6_blank_pilot.py``) with no ``--batch``
    opt-in at all, so unlike the nightly automation there is no scenario
    where this client should ever queue a real Batch API job. That is a
    different thing from forbidding the paid lane outright, though -- the
    project owner's own words: "no batch api ... for pilot go to paid on
    demand api, if free lane is already expired." So the paid lane itself
    stays open, synchronously, once the free lane's daily quota is spent;
    only real batch submission is refused. See ``BatchForbiddenError`` in
    ``src.llm.client`` for why this used to be ``forbid_paid_lane=True``
    (which forbade the paid lane outright, sync or batch) and why that was
    wrong: it made a spent free-lane quota degrade the whole run silently
    instead of continuing on-demand."""
    if (
        os.getenv("GEMINI_FREE_API_KEY")
        or os.getenv("GEMINI_PAID_API_KEY")
        or os.getenv("GEMINI_API_KEY")
    ):
        return GeminiLlmClient(forbid_paid_lane=False, forbid_batch=True)
    return None


def build_sentence_generator(llm_client: GeminiLlmClient | None) -> SentenceGenerator:
    """``LiveSentenceGenerator`` when ``llm_client`` is given, else the
    offline mock -- the same "real if configured, mock otherwise" shape
    ``run_pilot`` uses for its batch client."""
    if llm_client is not None:
        return LiveSentenceGenerator(llm_client)
    return MockSentenceGenerator()


# -- Pool variety: theme, person, tense, register, and sentence structure --
#
# Six grammatical-person perspectives (three singular, three plural) and
# five time-frame/mood hints, phrased as plain-language narrative direction
# rather than grammar instruction -- see the module docstring and
# ``build_prompt``'s docstring for why: naming "Perfekt" or "Dativ" in a
# generation prompt would be asking the model to produce a specific
# grammatical form on demand, exactly the thing this architecture avoids by
# computing the answer in code instead of trusting a model claim.
PERSON_PERSPECTIVES: tuple[tuple[str, str], ...] = (
    (
        "first_person_singular",
        "Write from one person's own point of view, telling about themselves.",
    ),
    (
        "second_person_singular_informal",
        "Write as direct, friendly observations or advice about the person you are talking to.",
    ),
    ("third_person_singular", "Write a short account about one other specific person."),
    (
        "first_person_plural",
        "Write from the point of view of a small group describing what they do together.",
    ),
    (
        "second_person_plural_informal",
        "Write as direct, friendly observations or advice about a small group you are talking to.",
    ),
    (
        "third_person_plural_or_formal",
        "Write a short account about several other people, or address someone politely "
        "and formally.",
    ),
)

TIME_FRAMES: tuple[tuple[str, str], ...] = (
    ("everyday_routine", "Describe an ordinary routine or habit, happening in the present."),
    ("recent_past", "Tell about something that already happened, as a short story about the past."),
    (
        "distant_past_before_past",
        "Tell about something that had already happened even earlier, before another past event.",
    ),
    ("near_future_plan", "Describe a plan or intention for the near future."),
    (
        "polite_or_hypothetical",
        "Phrase a wish, a polite request, or an imagined situation rather than a plain "
        "statement of fact.",
    ),
)

REGISTERS: tuple[tuple[str, str], ...] = (
    ("informal", "Use a casual, informal tone, as between friends."),
    ("formal", "Use a formal, polite tone, as in a letter or an announcement."),
)

STRUCTURES: tuple[tuple[str, str], ...] = (
    ("simple", "Keep each sentence to one main clause."),
    (
        "subordinate_clause",
        "Connect two ideas in one sentence using a word like 'weil', 'dass', 'wenn', or 'obwohl'.",
    ),
    (
        "relative_clause",
        "Add an extra detail about a person or thing using a clause introduced by "
        "'der', 'die', 'das', or a similar word.",
    ),
    ("coordinated_clauses", "Join two related actions in one sentence with 'und' or 'aber'."),
)

# ---------------------------------------------------------------------------
# Construction-aware generation: the fix for the audited pool-coverage gap
# (16 of 49 topics receiving zero items -- see the module docstring's "pool
# problem" section, which this section extends rather than duplicates).
# Theme/person/tense/register/structure above vary the SHAPE of ordinary
# prose; none of them makes a relative clause, a passive, or a Futur II more
# likely to appear, because a general-purpose pool of everyday sentences
# essentially never needs one -- more volume of "ordinary" sentences does
# not fix that, only asking for the construction does.
#
# Each entry below is a (topic_id, german_hint) pair. The FIRST element is
# never sent to the model -- exactly like every id in ``PERSON_PERSPECTIVES``,
# ``TIME_FRAMES``, ``REGISTERS`` and ``STRUCTURES`` above, only the second
# element of each of those tuples ever reaches ``build_prompt``'s output --
# it exists purely so this module's own source stays traceable to which
# starved topic each hint targets, for a human auditing coverage (or a test
# asserting a given topic's own hint text). The topic id appearing in this
# module's SOURCE is not a rule 2 violation: rule 2 is about what the model
# sees, and nothing here sends a topic id anywhere.
#
# The hint TEXT is what actually matters, and it is deliberately German
# (unlike the four hint axes above, whose text predates this task and
# stayed as originally written): every hint describes a communicative
# INTENT -- what a speaker is trying to say -- never the grammar that
# intent happens to fall out as. "Sag, was mit einer Sache passiert ist,
# ohne zu nennen, wer es getan hat" asks for a meaning; it does not ask for
# "the passive voice", and CLAUDE.md rule 2 (never name the grammar topic in
# anything the model sees) is why "the passive voice" could never appear
# here even as a paraphrase. ``test_every_construction_hint_avoids_grammar_
# terminology`` in the test suite checks every hint text below against the
# same forbidden-term list the rest of this module's hints are checked
# against.
#
# The audit named seventeen constructions as starved; sixteen hints are
# wired in below, matching the "16 of 49" figure exactly -- ``passiv_
# unpersoenlich`` (the impersonal passive: "Hier wird getanzt") is the
# seventeenth, and it is not one of the 49 ``TOPIC_IDS`` this pipeline
# covers at all, so it is not part of that "16 of 49" count either. It has
# no entry in ``selectors.SELECTORS`` (confirmed directly against that
# module -- ``"passiv_unpersoenlich" not in selectors.SELECTORS``), one of
# three topics cycle 3 excluded from this whole pipeline's scope for a
# tagger-level reason unrelated to pool coverage (see ``tests/
# test_blanking_pipeline.py``'s own comment on the three topics left out of
# ``TOPIC_IDS``). No sentence this module could ever generate can produce
# an item for a topic with no selector to select it -- wiring a live
# hint for it would spend real generation budget (CLAUDE.md 9's cost
# discipline) on sentences that structurally cannot become an item, so it is
# left out of the wired list. It is not left out of the offline mock pool
# below (``_STARVED_CONSTRUCTION_EXAMPLES``), for realism and so a future
# selector has real carrier-sound examples waiting -- see that constant's own
# comment for the honest limitation this implies for its own tests.
CONSTRUCTION_HINTS: tuple[tuple[str, str], ...] = (
    (
        "relativsatz_nom_akk",
        "Nenne zu einer Person oder Sache eine zusätzliche Information, indem du sagst, "
        "was sie selbst getan hat oder was jemand mit ihr gemacht hat.",
    ),
    (
        "relativsatz_dativ",
        "Beschreibe eine Person genauer, indem du sagst, wem sie geholfen hat oder wem sie "
        "etwas geschenkt, geschickt oder erklärt hat.",
    ),
    (
        "relativsatz_genitiv",
        "Stelle eine Person oder Sache vor, indem du erzählst, was mit etwas passiert ist, "
        "das ihr gehört, zum Beispiel ihr Auto, ihr Kind oder ihr Haus.",
    ),
    (
        "passiv_praesens",
        "Sag, was gerade mit einer Sache gemacht wird, ohne zu sagen, wer es tut.",
    ),
    (
        "passiv_praeteritum",
        "Erzähl, was früher einmal mit einer Sache gemacht wurde, ohne zu sagen, wer es getan hat.",
    ),
    (
        "passiv_modalverben",
        "Sag, was mit einer Sache gemacht werden muss, kann oder soll, ohne zu sagen, wer "
        "das tun soll.",
    ),
    (
        "zustandspassiv",
        "Beschreibe den Zustand, in dem sich etwas jetzt befindet, weil vorher etwas damit "
        "gemacht wurde, zum Beispiel repariert, geöffnet oder fertig.",
    ),
    (
        "zustandspassiv_zeiten",
        "Beschreibe, in welchem Zustand etwas schon zu einem früheren Zeitpunkt war, weil "
        "vorher etwas damit gemacht worden war.",
    ),
    (
        "infinitiv_mit_zu",
        "Sag, was jemand plant, hofft, versucht oder schwierig findet zu tun.",
    ),
    (
        "infinitiv_um_zu",
        "Erklär, warum jemand etwas getan hat: was war sein Ziel oder sein Grund dafür?",
    ),
    (
        "partizip_i_attributiv",
        "Beschreibe eine Person oder Sache mit einem einzigen Wort direkt vor dem Nomen, "
        "das ausdrückt, was sie gerade tut, zum Beispiel das lachende Kind oder der "
        "wartende Mann.",
    ),
    (
        "partizip_ii_attributiv_erweitert",
        "Beschreibe eine Sache mit mehreren Wörtern direkt vor dem Nomen, die ausdrücken, "
        "was mit ihr schon gemacht wurde und von wem, zum Beispiel das von einer Firma "
        "gebaute Haus.",
    ),
    (
        "kasus_genitiv_formen",
        "Sag, wem oder wozu etwas gehört, oder wessen Ergebnis, Farbe oder Titel du meinst.",
    ),
    (
        "praepositionen_genitiv_gehoben",
        "Schreibe in einem sehr formellen, offiziellen Ton, wie in einem Bericht oder einer "
        "amtlichen Mitteilung, und nenne dabei einen Grund oder verweise auf Unterlagen.",
    ),
    (
        "konjunktiv_ii_hoeflichkeit",
        "Bitte eine fremde Person sehr höflich um etwas, oder formuliere eine vorsichtige, "
        "zurückhaltende Bitte.",
    ),
    (
        "futur_ii",
        "Sag, dass etwas bis zu einem bestimmten Zeitpunkt in der Zukunft schon fertig oder "
        "erledigt sein wird.",
    ),
    # -- Appended by a later cycle (feat/generate-then-blank): the three
    # Nominative article topics, not part of the "16 of 49" audit above and
    # not "starved" in that audit's sense (a general-purpose sentence pool
    # contains plenty of plain Nominative sentences). Their own defect was
    # architectural (module docstring's "pool problem" does not apply to
    # them at all): ``pipeline.py`` used to report zero for all three
    # unconditionally, because no single bare sentence forces "der" over
    # "ein"/"kein"/a possessive (blanker.py's own module docstring, final
    # section). ``selectors.py`` now gives each of the three its own
    # structural forcing anchor (a uniqueness-making relative clause /
    # superlative / ordinal for the definite article, a causal "weil" clause
    # for the negative article, a kinship noun plus an explicit 1st/2nd
    # person reference for the possessive), and these three hints nudge
    # generation toward writing the CARRIER SHAPE each anchor needs -- still
    # by describing communicative intent only, never the grammar it falls
    # out as, exactly like every hint above.
    (
        "artikel_bestimmt_nom",
        "Beschreibe eine Person oder Sache so genau, dass völlig klar ist, welche einzige "
        "gemeint ist: füge direkt danach eine zusätzliche Information hinzu -- was sie selbst "
        "getan hat oder was jemand mit ihr gemacht hat -- oder sag, dass sie in ihrer Art die "
        "größte, älteste, beste, erste oder letzte ist.",
    ),
    (
        "artikel_unbestimmt_kein_nom",
        "Erklär in einem Satz, warum jemand etwas nicht tun kann oder etwas nicht passiert, "
        "weil eine Sache oder Person komplett fehlt oder überhaupt nicht vorhanden ist -- "
        "nenne sowohl diesen Grund als auch die Folge davon.",
    ),
    (
        "artikel_possessiv_nom",
        "Stelle ein Familienmitglied vor (zum Beispiel Mutter, Vater, Bruder, Schwester, "
        "Großmutter, Großvater, Onkel oder Tante) und füge direkt danach eine zusätzliche "
        "Information hinzu, die zeigt, dass genau du oder deine Gesprächspartnerin oft mit "
        "dieser Person zu tun hat -- zum Beispiel wen sie regelmäßig besucht, anruft oder "
        "trifft.",
    ),
    # -- Appended by a later cycle (feat/generate-then-blank): the loop over
    # the topic list needs EVERY topic with a selector to have a construction
    # hint (see this module's own top-of-file plan), not only the starved-
    # construction and Nominative-article topics above. These 30 cover every
    # remaining topic in ``selectors.SELECTORS`` that had no hint yet --
    # tenses, cases, prepositions, pronouns, adjective declension, and finite
    # verb conjugation -- confirmed reachable exactly like every hint above:
    # each has hand-written examples in ``_STARVED_CONSTRUCTION_EXAMPLES``
    # (despite most of these not being "starved" in that dict's original,
    # narrower sense -- reused as the one dict this module already has for
    # "hand-written examples proven to produce a candidate, keyed by topic"),
    # every one individually run through ``carrier_validation.validate_carrier``
    # (accepted) and its own topic's selector (candidate found) before being
    # added, per ``tests/test_blanking_sentence_source.py``'s own per-topic
    # parametrised proof.
    (
        "perfekt_sein",
        "Erzähl im Gespräch, so wie man es einer Freundin oder einem Freund mündlich "
        "berichten würde, dass eine Person irgendwohin gefahren, gegangen, geflogen oder "
        "gekommen ist und inzwischen dort angekommen ist, oder dass sich ihr Zustand "
        "verändert hat, zum Beispiel dass sie aufgewacht ist.",
    ),
    (
        "perfekt_haben",
        "Erzähl im Gespräch, so wie man es einer Freundin oder einem Freund mündlich "
        "berichten würde, was jemand heute oder gestern schon erledigt, gemacht oder "
        "geschafft hat.",
    ),
    (
        "praeteritum_vollverben",
        "Schreibe wie in einer Geschichte oder einem Zeitungsbericht, nicht wie in einem "
        "Gespräch, und erzähle darin, was früher einmal geschah oder wie etwas ablief.",
    ),
    (
        "praeteritum_sein_haben_modal",
        "Schreibe wie in einer Geschichte oder einem schriftlichen Bericht und beschreibe "
        "darin, wie jemand damals war, was jemand damals hatte, oder was jemand damals "
        "wollte, konnte, musste oder durfte.",
    ),
    (
        "plusquamperfekt",
        "Erzähl von zwei vergangenen Ereignissen und mach deutlich, dass das eine schon "
        "vorbei war, bevor das andere überhaupt begann.",
    ),
    (
        "futur_i",
        "Versprich einer bestimmten Person etwas für einen genau genannten späteren "
        "Zeitpunkt, oder beschreibe einen festen Plan dafür.",
    ),
    (
        "konjunktiv_ii_irreal_gegenwart",
        "Beschreibe, was jetzt gerade anders wäre, wenn eine bestimmte Sache im Moment "
        "anders wäre, als sie wirklich ist.",
    ),
    (
        "konjunktiv_ii_vergangenheit",
        "Beschreibe, wie etwas anders ausgegangen wäre, wenn eine frühere Situation anders "
        "verlaufen wäre.",
    ),
    (
        "adjektiv_komparativ_superlativ",
        "Vergleiche zwei oder mehrere Dinge oder Personen miteinander und sag, welche davon "
        "eine bestimmte Eigenschaft am stärksten hat.",
    ),
    (
        "adjektivdeklination_bestimmt",
        "Beschreibe eine ganz bestimmte, bereits bekannte Person oder Sache genauer, zum "
        "Beispiel mit einer Farbe, einer Größe oder einer anderen Eigenschaft.",
    ),
    (
        "adjektivdeklination_unbestimmt",
        "Beschreibe eine neue, bisher noch nicht genannte Person oder Sache genauer und "
        "erwähne sie zum ersten Mal, zum Beispiel mit einer Farbe, einer Größe oder einer "
        "anderen Eigenschaft.",
    ),
    (
        "adjektivdeklination_nullartikel",
        "Beschreibe allgemein eine Menge einer Sache, ohne sie zu zählen oder eine einzelne "
        "davon zu meinen, zum Beispiel eine Speise, ein Getränk oder ein Material, und füge "
        "eine Eigenschaft davor hinzu.",
    ),
    (
        "nomen_plural",
        "Sprich über mehrere gleichartige Personen oder Dinge gleichzeitig, nicht nur über "
        "eine einzelne.",
    ),
    (
        "kasus_akkusativ_formen",
        "Sag, was jemand mit einer Sache oder Person direkt macht, zum Beispiel was er "
        "sieht, kauft, liest oder nimmt.",
    ),
    (
        "kasus_dativ_formen",
        "Sag, wem etwas gegeben, gezeigt, erklärt oder geholfen wird.",
    ),
    (
        "praepositionen_genitiv",
        "Schreib in einem ganz normalen, alltäglichen Ton -- nicht besonders förmlich -- "
        "und nenne einen Grund, einen Zeitraum oder etwas, das trotzdem passiert oder "
        "stattdessen gemacht wird.",
    ),
    (
        "praepositionen_akkusativ",
        "Sag, für wen etwas gedacht ist, wogegen jemand ist, wodurch jemand geht oder "
        "fährt, ohne was jemand etwas tut, oder bis wann etwas dauert.",
    ),
    (
        "praepositionen_dativ",
        "Sag, bei wem jemand ist oder wohnt, mit wem oder womit jemand etwas macht, woher "
        "etwas kommt, seit wann etwas so ist, oder zu wem jemand unterwegs ist.",
    ),
    (
        "akkusativ_nach_praeposition",
        "Beschreibe, wohin sich etwas oder jemand bewegt oder wohin etwas gelegt, gestellt "
        "oder gehängt wird.",
    ),
    (
        "dativ_nach_praeposition",
        "Beschreibe, wo sich etwas oder jemand gerade befindet oder wo etwas bereits "
        "liegt, steht oder hängt, ohne dass sich etwas dorthin bewegt.",
    ),
    (
        "pronomen_personal_nom",
        "Erwähne eine Person oder Sache noch einmal, ohne ihren Namen zu wiederholen, als "
        "diejenige, die selbst etwas tut.",
    ),
    (
        "pronomen_personal_akk",
        "Erwähne eine Person oder Sache noch einmal, ohne ihren Namen zu wiederholen, als "
        "diejenige, die jemand sieht, kennt, besucht oder auf eine andere Weise direkt "
        "betrifft.",
    ),
    (
        "pronomen_personal_dat",
        "Erwähne eine Person noch einmal, ohne ihren Namen zu wiederholen, als diejenige, "
        "der etwas gegeben, geschenkt oder geschickt wird.",
    ),
    (
        "modalverben_praesens",
        "Sag, was jemand gerade will, muss, darf, kann oder soll.",
    ),
    (
        "verb_praesens_regelm",
        "Beschreibe eine gewöhnliche Tätigkeit, die eine Person regelmäßig in ihrem Alltag macht.",
    ),
    (
        "verb_praesens_vokalwechsel",
        "Sprich eine einzelne andere Person direkt an, oder erzähle über eine einzelne "
        "andere Person (er oder sie), was genau diese eine Person regelmäßig in ihrem "
        "Alltag macht -- nie über dich selbst und nie über mehrere Personen gemeinsam.",
    ),
    (
        "verb_sein_haben",
        "Beschreibe knapp, wie jemand gerade ist oder was jemand gerade hat oder besitzt.",
    ),
    (
        "verben_trennbar_praesens",
        "Beschreibe eine gewöhnliche Alltagshandlung, bei der jemand morgens aufsteht, "
        "abends aufräumt, jemanden anruft oder einlädt, oder das Licht anmacht oder "
        "ausmacht.",
    ),
    (
        "verben_reflexiv_akk",
        "Beschreibe ein Gefühl oder eine Reaktion, die eine Person bei sich selbst auslöst "
        "oder erlebt, zum Beispiel dass sie sich freut, sich ärgert, sich beeilt oder sich "
        "mit jemandem trifft.",
    ),
    (
        "verben_reflexiv_dat",
        "Beschreibe, dass jemand sich selbst etwas kauft, sich etwas vorstellt, sich etwas "
        "überlegt oder sich etwas leiht.",
    ),
)

DEFAULT_THEMES: tuple[str, ...] = (
    "Alltag",
    "Reisen",
    "Arbeit",
    "Familie",
    "Gesundheit",
    "Essen",
    "Wetter",
    "Freizeit",
    "Umwelt",
    "Technologie",
    "Einkaufen",
    "Wohnen",
    "Schule",
    "Nachrichten",
    "Sport",
    "Freundschaft",
)

# The generation-track plan's own target (docs/audits/generation-track-plan.md:
# "300 candidates per pilot"), and comfortably "well above 60" -- the size
# and uniformity of the pool that produced the 44-item/1-item topic skew
# this module exists to fix.
DEFAULT_POOL_SIZE = 300

# TASK 3: fewer sentences per call, so the model juggles fewer simultaneous
# constraints (JSON validity, a count, a theme, a CEFR level, and German
# grammar) for less of its output before quality starts to decay. The
# audited pilot asked for ~60 sentences in ONE call; lowered here from this
# module's own prior default of 10 to 6 -- a substantial cut from the
# original defect (90% fewer sentences per call than the audited pilot) and
# still a real cut from where this module already stood (40% fewer than 10),
# not a token gesture in either direction.
#
# The tradeoff is wall clock, not quality, and it is real: at
# ``DEFAULT_POOL_SIZE`` (300) this raises the number of
# ``generate_sentence_pool`` calls from ceil(300/10)=30 to ceil(300/6)=50.
# The free lane's ceiling (``GeminiLlmClient.FREE_LANE_RATE_LIMIT_PER_MINUTE``,
# operator-tuned and pinned, NOT changed here) is 5 requests/minute
# regardless of how many of those requests are in flight at once
# (``FREE_LANE_MAX_CONCURRENCY`` only affects how evenly they arrive, not how
# many clear per minute) -- so a full pool build goes from roughly 30/5 = 6
# minutes of wall clock to roughly 50/5 = 10 minutes. Choosing 6 over an even
# smaller batch size (e.g. 3, which would roughly double the call count
# again to ~17 minutes) is the point at which this trade stops paying for
# itself: each further halving of batch size buys a shrinking reduction in
# per-call complexity for a full doubling of wall clock, and 6 sentences is
# already a small enough list that late-list decay is unlikely to be the
# dominant source of error the way it plausibly was at 60.
DEFAULT_BATCH_SIZE = 6


@dataclass
class SentencePool:
    """A large, themed, person/tense-varied, carrier-validated pool, and the
    bookkeeping a pilot report needs to show what building it cost and what
    it lost: every sentence generation batch run, how many raw sentences
    came back before validation and deduplication, how many exact duplicates
    were dropped, and -- the number CLAUDE.md's kill-criteria honesty rule
    (section 12) asks for -- a count of every carrier rejected, by reason."""

    sentences: list[str] = field(default_factory=list)
    requested: int = 0
    raw_generated: int = 0
    duplicates_skipped: int = 0
    rejected_by_reason: Counter[str] = field(default_factory=Counter)
    batches_run: int = 0

    @property
    def accepted_count(self) -> int:
        return len(self.sentences)


def _accepts_construction_hint(generator: SentenceGenerator) -> bool:
    """Whether ``generator.generate`` actually accepts the ``construction``
    keyword this module added alongside person/tense/register/structure.

    Every implementation of the ``SentenceGenerator`` Protocol above SHOULD
    accept it, but a concrete generator written before this parameter
    existed -- a test double built against the four-hint Protocol, in
    particular -- would otherwise raise ``TypeError`` the moment
    ``generate_sentence_pool`` started passing a fifth keyword it never
    declared. Checked once per ``generate_sentence_pool`` call, not once per
    batch, since a generator's own signature cannot change mid-run. Never
    raises: a callable ``inspect.signature`` genuinely cannot introspect (a
    C-implemented callable with no Python signature) degrades to "assume
    yes", matching the Protocol's own contract, rather than silently
    dropping a hint from a generator that actually does support it."""
    try:
        parameters = inspect.signature(generator.generate).parameters
    except (TypeError, ValueError):
        return True
    return "construction" in parameters or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()
    )


def generate_sentence_pool(
    generator: SentenceGenerator,
    cefr: CEFR,
    total: int = DEFAULT_POOL_SIZE,
    *,
    themes: Sequence[str] = DEFAULT_THEMES,
    batch_size: int = DEFAULT_BATCH_SIZE,
    validate: bool = True,
) -> SentencePool:
    """Build a large, varied, carrier-validated sentence pool, replacing one
    big single-theme request with many small ones that each nudge a
    different theme, narrative person, time frame, register, sentence
    structure, and -- cycling through ``CONSTRUCTION_HINTS`` -- a specific
    construction to write toward, one per call. The first fix addresses the
    pilot's 44-item/1-item topic skew (module docstring): person and tense
    variety are properties of what the model was asked to write, and one
    uniform request cannot produce them no matter how large ``total`` is.
    The construction hints address a DIFFERENT gap the same fix cannot
    reach: a request for varied everyday prose, however varied, essentially
    never contains a relative clause, a passive, or a Futur II, because
    ordinary daily narration rarely needs one -- these have to be asked for
    directly, by describing the communicative intent that construction
    expresses (see ``CONSTRUCTION_HINTS``'s own comment for why that is not
    a rule 2 violation).

    Cost is bounded and predictable on purpose: exactly
    ``ceil(total / batch_size)`` calls to ``generator.generate`` are made, no
    more, regardless of how many carriers ``carrier_validation`` ends up
    rejecting. This deliberately does NOT loop to backfill a shortfall after
    validation losses -- CLAUDE.md 9's cost discipline (a nightly cap
    "independent of the spend ceiling, so a logic bug cannot spend the month
    in one night") is exactly the property an adaptive retry-until-full loop
    would give up for a live caller. A live run that loses a lot of its pool
    to validation ends up short of ``total``; that shortfall is reported
    (``requested`` vs ``accepted_count``), not silently backfilled by
    spending more.

    Every accepted sentence has passed
    ``carrier_validation.validate_carrier`` (task 1's carrier-soundness
    check) when ``validate=True`` (the default) -- this is the point at
    which carrier validation actually runs, before anything downstream ever
    sees a sentence. ``validate=False`` exists only for a caller that wants
    to inspect the raw, unvalidated pool (e.g. to audit what validation is
    discarding), never for production use.

    Deduplicates by exact sentence text, in the order sentences were
    generated, so the same carrier does not recur across many items the way
    the audited pilot's did.
    """
    pool = SentencePool(requested=max(total, 0))
    if total <= 0:
        return pool

    seen: set[str] = set()
    num_batches = -(
        -total // batch_size
    )  # ceil division, no negative-total edge case (guarded above)
    construction_supported = _accepts_construction_hint(generator)

    for cell_index in range(num_batches):
        theme = themes[cell_index % len(themes)]
        _, person_hint = PERSON_PERSPECTIVES[cell_index % len(PERSON_PERSPECTIVES)]
        _, tense_hint = TIME_FRAMES[cell_index % len(TIME_FRAMES)]
        _, register_hint = REGISTERS[cell_index % len(REGISTERS)]
        _, structure_hint = STRUCTURES[cell_index % len(STRUCTURES)]
        _, construction_hint = CONSTRUCTION_HINTS[cell_index % len(CONSTRUCTION_HINTS)]

        if construction_supported:
            raw = generator.generate(
                cefr,
                theme,
                batch_size,
                person=person_hint,
                tense=tense_hint,
                register=register_hint,
                structure=structure_hint,
                construction=construction_hint,
            )
        else:
            raw = generator.generate(
                cefr,
                theme,
                batch_size,
                person=person_hint,
                tense=tense_hint,
                register=register_hint,
                structure=structure_hint,
            )
        pool.raw_generated += len(raw)
        pool.batches_run += 1

        candidates = raw
        if validate:
            summary = carrier_validation.validate_carriers(raw)
            candidates = summary.accepted
            pool.rejected_by_reason.update(summary.rejected_by_reason)

        for sentence in candidates:
            if sentence in seen:
                pool.duplicates_skipped += 1
                continue
            seen.add(sentence)
            pool.sentences.append(sentence)
            if len(pool.sentences) >= total:
                return pool

    return pool
