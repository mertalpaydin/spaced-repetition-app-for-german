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
import json
import os
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from src.contracts import CEFR, MODEL_GENERATE
from src.generation.blanking import carrier_validation
from src.llm.client import GeminiLlmClient

_INSTRUCTION = (
    "Write natural, grammatically correct German sentences for a language "
    "learner. Each sentence must be a complete, plain statement -- no gaps, "
    "no blanks, no underscores, no questions to the reader. Do not mention "
    "grammar, cases, articles, tenses, or any linguistic terminology "
    "anywhere in your output; just write ordinary German sentences a "
    "textbook would use as reading material. Respond with ONLY a JSON "
    'object of the exact shape {"sentences": ["...", "..."]} and nothing '
    "else -- no commentary, no markdown fence."
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
) -> str:
    """The full generation prompt: CEFR level and theme are the only
    grammar-adjacent-looking inputs when the four optional hints are left at
    their default of ``None`` -- unchanged from before ``generate_sentence_pool``
    existed, byte for byte, so every existing caller's prompt is identical.

    The four keyword-only hints are how ``generate_sentence_pool`` steers
    person, tense, register, and sentence structure without ever naming a
    grammar topic (see the module docstring): each is a plain-language
    nudge ("write about yesterday", "address a close friend directly"), not
    a grammar instruction ("use the Perfekt", "use the Dativ")."""
    lines = [_INSTRUCTION, "", f"CEFR level: {cefr}", f"Theme: {theme}"]
    if person is not None:
        lines.append(f"Perspective: {person}")
    if tense is not None:
        lines.append(f"Time frame: {tense}")
    if register is not None:
        lines.append(f"Register: {register}")
    if structure is not None:
        lines.append(f"Sentence shape: {structure}")
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
    ) -> list[str]:
        prompt = build_prompt(
            cefr, theme, count, person=person, tense=tense, register=register, structure=structure
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
_MOCK_SENTENCE_POOL: tuple[str, ...] = (
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


class MockSentenceGenerator:
    """Deterministic, offline sentence pool (CLAUDE.md 7: unit tests never
    touch the network), mirroring the pattern already used by
    ``src.generation.batch_client.MockBatchClient`` -- a fixed, inspectable
    substitute for the real model, never randomness, so a run with the same
    arguments is exactly reproducible.

    Unlike a single fixed slice of the pool, the starting position is a
    deterministic hash of every argument that identifies "which request this
    is" (``cefr``, ``theme``, and the four variety hints) -- not the
    sentence content, which this offline mock cannot actually vary to match
    a hint the way a real model would. This is what lets
    ``generate_sentence_pool``'s many differently-themed batch calls surface
    different slices of the 177-sentence pool offline too, instead of the
    first ``count`` sentences over and over regardless of theme (which would
    make every batch collapse to the same handful of duplicates after
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
    ) -> list[str]:
        pool = _MOCK_SENTENCE_POOL
        if count <= 0 or not pool:
            return []
        key = "|".join(str(part) for part in (cefr, theme, person, tense, register, structure))
        offset = int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16) % len(pool)
        return [pool[(offset + i) % len(pool)] for i in range(count)]


def client_from_env() -> GeminiLlmClient | None:
    """Build a real ``GeminiLlmClient`` only when a lane key is actually
    configured in the environment; ``None`` otherwise. Mirrors
    ``src.generation.batch_client._build_llm_client_if_configured`` exactly
    -- kept as its own small copy here rather than importing that private
    helper, so this module stays self-contained within the new package.

    Built with ``forbid_paid_lane=True``: this is a pilot script
    (``scripts/step6_blank_pilot.py``) with no ``--batch`` opt-in at all, so
    unlike the nightly automation there is no scenario where this client
    should ever fall through to the real paid Batch API. If the free lane's
    daily quota is exhausted, the run must fail loudly, not spend silently."""
    if (
        os.getenv("GEMINI_FREE_API_KEY")
        or os.getenv("GEMINI_PAID_API_KEY")
        or os.getenv("GEMINI_API_KEY")
    ):
        return GeminiLlmClient(forbid_paid_lane=True)
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
DEFAULT_BATCH_SIZE = 10


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
    different theme, narrative person, time frame, register, and sentence
    structure -- the fix for the pilot's 44-item/1-item topic skew (module
    docstring): person and tense variety are properties of what the model
    was asked to write, and one uniform request cannot produce them no
    matter how large ``total`` is.

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

    for cell_index in range(num_batches):
        theme = themes[cell_index % len(themes)]
        _, person_hint = PERSON_PERSPECTIVES[cell_index % len(PERSON_PERSPECTIVES)]
        _, tense_hint = TIME_FRAMES[cell_index % len(TIME_FRAMES)]
        _, register_hint = REGISTERS[cell_index % len(REGISTERS)]
        _, structure_hint = STRUCTURES[cell_index % len(STRUCTURES)]

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
