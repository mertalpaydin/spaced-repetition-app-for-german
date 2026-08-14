"""Layer 2: Morphosyntactic consistency and grammatical agreement validator."""

import re

from src.contracts import CandidateItem, Topic


class Layer2MorphologyValidator:
    """Validates that candidate completions satisfy German morphosyntactic rules."""

    def validate(self, item: CandidateItem, topic: Topic | None = None) -> tuple[bool, str | None]:
        """Validate morphosyntactic consistency of the completed sentence."""
        # 1. Check insertion sanity
        filled_sentence = item.prompt.replace("___", item.proposed_answer)
        if "___" in filled_sentence:
            # Multi-gap or unreplaced
            return False, "Unfilled gaps remaining after answer insertion."

        # 2. Check capitalisation / casing consistency
        words = filled_sentence.split()
        if not words:
            return False, "Empty completed sentence."

        # Verify initial capital letter
        if not words[0][0].isupper():
            return False, "Completed sentence does not begin with an uppercase letter."

        # Verify terminal punctuation (. ! ?)
        if filled_sentence.rstrip()[-1] not in ".!?\"'":
            return False, "Completed sentence lacks terminal punctuation."

        # 3. Check topic-specific morphological constraints if topic provided
        if topic and topic.morph_spec:
            ans_clean = item.proposed_answer.strip().lower()
            prompt_lower = item.prompt.lower()

            # Case requirements for articles/pronouns
            case_list = topic.morph_spec.get("Case") or []
            if "Dat" in case_list and topic.id == "dativ_nach_praeposition":
                # Detect directional verbs (Wohin? requiring Akkusativ)
                directional_verbs = {
                    "stellt",
                    "stellen",
                    "legt",
                    "legen",
                    "setzt",
                    "setzen",
                    "steckt",
                    "stecken",
                }
                tokens = set(re.findall(r"\b\w+\b", prompt_lower))
                if tokens & directional_verbs:
                    return False, "Directional verb (Wohin?) requires Akkusativ, not Dativ."

                # Check for singular masculine/neuter noun following the gap
                masc_neut_sg = {
                    "tisch",
                    "boden",
                    "stuhl",
                    "schreibtisch",
                    "garten",
                    "hund",
                    "park",
                    "teppich",
                    "schrank",
                    "bahnhof",
                    "vater",
                    "haus",
                    "kind",
                }
                next_word_match = re.search(r"___\s+([A-ZÄÖÜa-zäöüß]+)", item.prompt)
                next_word = next_word_match.group(1).lower() if next_word_match else ""

                if next_word in masc_neut_sg:
                    if ans_clean not in {"dem", "einem", "meinem", "seinem", "ihrem", "unserem"}:
                        ans = item.proposed_answer
                        return (
                            False,
                            f"Proposed answer '{ans}' is not valid singular Dativ for noun.",
                        )
                else:
                    dat_forms = {
                        "dem",
                        "der",
                        "den",
                        "einem",
                        "einer",
                        "meinem",
                        "meiner",
                        "seinem",
                        "seiner",
                        "ihrem",
                    }
                    if ans_clean not in dat_forms:
                        ans = item.proposed_answer
                        return False, f"Proposed answer '{ans}' is not a valid Dativ form."

            if "Acc" in case_list and topic.id == "akkusativ_nach_praeposition":
                # Detect static verbs (Wo? requiring Dativ)
                static_verbs = {
                    "liegt",
                    "liegen",
                    "steht",
                    "stehen",
                    "sitzt",
                    "sitzen",
                    "bleibt",
                    "bleiben",
                }
                tokens = set(re.findall(r"\b\w+\b", prompt_lower))
                if tokens & static_verbs:
                    return False, "Static verb (Wo?) requires Dativ, not Akkusativ."

                masc_sg = {
                    "tisch",
                    "boden",
                    "stuhl",
                    "schreibtisch",
                    "garten",
                    "hund",
                    "park",
                    "teppich",
                    "schrank",
                    "bahnhof",
                    "vater",
                    "mitarbeiter",
                }
                next_word_match = re.search(r"___\s+([A-ZÄÖÜa-zäöüß]+)", item.prompt)
                next_word = next_word_match.group(1).lower() if next_word_match else ""

                if next_word in masc_sg:
                    if ans_clean not in {"den", "einen", "meinen", "seinen", "ihren", "unseren"}:
                        ans = item.proposed_answer
                        return (
                            False,
                            f"Proposed answer '{ans}' is not valid Akkusativ for masculine.",
                        )
                else:
                    acc_forms = {
                        "den",
                        "die",
                        "das",
                        "einen",
                        "eine",
                        "ein",
                        "meinen",
                        "meine",
                        "sein",
                        "seinen",
                    }
                    if ans_clean not in acc_forms:
                        ans = item.proposed_answer
                        return False, f"Proposed answer '{ans}' is not a valid Akkusativ form."

            # Separable prefix check
            if topic.morph_spec.get("Separable") and topic.id == "verben_trennbar_praesens":
                if not re.search(r"\b(an|auf|aus|ein|ab|mit|vor|zu|fern)\b", ans_clean):
                    ans = item.proposed_answer
                    return False, f"Proposed answer '{ans}' lacks separable prefix."

        return True, None
