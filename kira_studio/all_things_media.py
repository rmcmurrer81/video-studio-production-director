"""Pure media-planning helpers for the All Things Agentic edition.

The Cloud adapters own storage, Text-to-Speech, and FFmpeg execution.  This
module deliberately keeps screenplay dialogue extraction and narrated-card
cue construction deterministic and testable without network access.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence


_SCENE_HEADING = re.compile(
    r"^(?:\d+[.)]?\s*)?(?:INT\.?|EXT\.?|INT\.?/EXT\.?|I/E\.?|EST\.?)[\s./-]+",
    re.IGNORECASE,
)
_TRANSITION = re.compile(
    r"^(?:CUT TO|FADE IN|FADE OUT|DISSOLVE TO|SMASH CUT|MATCH CUT|END|THE END)\s*:?$",
    re.IGNORECASE,
)
_CHARACTER_CUE = re.compile(
    r"^[A-Z][A-Z0-9 ._'\-]{1,47}(?:\s*\([^)]{1,80}\))?$"
)
_FDX_CHARACTER = re.compile(r"^\[Character\]\s+(.+)$", re.IGNORECASE)
_FDX_DIALOGUE = re.compile(r"^\[Dialogue\]\s+(.+)$", re.IGNORECASE)
_FDX_SCENE_HEADING = re.compile(r"^\[Scene Heading\]\s+(.+)$", re.IGNORECASE)
_NATURAL_SCENE_MARKER = re.compile(
    r"(?<![A-Za-z0-9_])(SCENE\s+\d{1,3}\s*:)",
    re.IGNORECASE,
)
_NATURAL_SCENE_HEADING = re.compile(r"^SCENE\s+\d{1,3}\s*:$", re.IGNORECASE)
_QUOTED_DIALOGUE = re.compile(
    r'(?:"([^"\n]{2,500})"|“([^”\n]{2,500})”|‘([^’\n]{2,500})’)'
)
_ATTRIBUTED_QUOTED_DIALOGUE = re.compile(
    r"\b([A-Z][A-Za-z'_\-]{0,39}(?:\s+[A-Z][A-Za-z'_\-]{0,39}){0,3})\s+"
    r"(?:(?i:says|said|asks|asked|answers|answered|replies|replied)|"
    r"(?i:tells|told)\s+[A-Z][A-Za-z'_\-]{0,39})\s*,?\s*"
    r'(?:"([^"\n]{2,500})"|“([^”\n]{2,500})”|‘([^’\n]{2,500})’)'
)


_SHOT_ID = re.compile(r"\bSC\d{1,3}-SH\d{1,3}\b", re.IGNORECASE)
_SHOT_NUMBER = re.compile(r"\bSH0*(\d{1,3})\b", re.IGNORECASE)
_SHOT_DIRECTIVE = re.compile(
    r"\bshot\s*0*(?P<label>\d{1,3}(?:\s*\.\s*0*\d{1,3})?)\s*"
    r"[:\-–—)]\s*",
    re.IGNORECASE,
)
_SCENEWIDE_SHOT_LIST = re.compile(
    r"\bshots?\s+\d{1,3}(?:\s*/\s*\d{1,3}){1,}",
    re.IGNORECASE,
)
_RETURN_LOCATION = re.compile(r"^\s*back\s+in\s+", re.IGNORECASE)
_LOCATION_TIME_SUFFIX = re.compile(
    r"\s*,\s*(?:continuous|same|later|moments? later|"
    r"(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?) later|"
    r"dawn|morning|day|afternoon|evening|dusk|night)\s*$",
    re.IGNORECASE,
)
_INTERNAL_SPOKEN_REWRITES = (
    (re.compile(r"\bprimary[ _-]+coverage\b", re.IGNORECASE), "main moment"),
    (re.compile(r"\bcontinuity[ _-]+bridge\b", re.IGNORECASE), "transition"),
    (re.compile(r"\bcanon(?:ical)?\b", re.IGNORECASE), "story continuity"),
    (re.compile(r"\bestablishing\b", re.IGNORECASE), "opening"),
    (re.compile(r"\bestablished\b", re.IGNORECASE), "known"),
    (re.compile(r"\bestablishes\b", re.IGNORECASE), "introduces"),
    (re.compile(r"\bestablish\b", re.IGNORECASE), "introduce"),
)
_ESTABLISH_ACTION = re.compile(
    r"^Establish\s+(.+?)\s+and the spatial relationship of\s+(.+?)\s+"
    r"before this scene beat:\s*(.+)$",
    re.IGNORECASE,
)
_PRIMARY_ACTION = re.compile(
    r"^Stage\s+(.+?)\s+in\s+(.+?)\s+for the primary scene beat:\s*(.+)$",
    re.IGNORECASE,
)
_BRIDGE_ACTION = re.compile(
    r"^Hold a reaction, prop, or environmental detail in\s+(.+?)\s+"
    r"that directly supports this beat:\s*(.+)$",
    re.IGNORECASE,
)
_BRIEF_AUDIO = re.compile(r"\bBrief audio direction:\s*(.+)$", re.IGNORECASE)
_PRODUCTION_DIRECTIVE = re.compile(
    r"\b(?:introduc(?:e|es|ed|ing)|establish(?:es|ed|ing)?|stag(?:e|es|ed|ing)|"
    r"deliver(?:s|ed|ing)?|resolve\s+(?:(?:the|this)\s+)?(?:scene|story|beat|"
    r"(?:dramatic\s+)?(?:tension|conflict))|"
    r"show\s+(?:the\s+)?resolution|hold\s+(?:a\s+)?reaction)\b",
    re.IGNORECASE,
)
_PRODUCTION_META = re.compile(
    r"\b(?:primary[ _-]+coverage|continuity[ _-]+bridge|scene beat|"
    r"production purpose|storyboard card|camera coverage|brief audio direction)\b",
    re.IGNORECASE,
)

_ESTABLISH_OPENINGS = (
    "{characters} enter {setting}.",
    "In {setting}, {characters} face the next turn.",
    "{characters} choose their path in {setting}.",
)
_PRIMARY_OPENINGS = (
    "{characters} act.",
    "{characters} respond.",
    "{characters} decide.",
)
_BRIDGE_OPENINGS = (
    "A detail reveals danger.",
    "A reaction carries the tension forward.",
    "A final detail confirms their choice.",
)
def _clean(value: Any, *, maximum: int = 1_800) -> str:
    text = str(value or "").replace("\x00", " ")
    text = re.sub(r"[\t ]+", " ", text)
    return text.strip()[:maximum]


def _speaker_name(value: str) -> str:
    speaker = re.sub(r"\s*\([^)]*\)\s*$", "", value).strip()
    return _clean(speaker, maximum=80)


def _spoken_story_text(value: Any, *, maximum: int = 1_800) -> str:
    """Remove production-only labels from audience-facing spoken text."""

    text = _SHOT_ID.sub("", _clean(value, maximum=maximum))
    text = _SHOT_DIRECTIVE.sub("", text)
    for pattern, replacement in _INTERNAL_SPOKEN_REWRITES:
        text = pattern.sub(replacement, text)
    text = re.sub(r"\bcard\s+\d+\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" ,;-\t")
    return text[:maximum]


def _sentence(value: Any, *, maximum: int = 1_800) -> str:
    text = _spoken_story_text(value, maximum=maximum).strip()
    terminal = text.rstrip("\"'")
    if text and not terminal.endswith((".", "!", "?")):
        text += "."
    return text


def _spoken_characters(value: Any) -> str:
    names = [part.strip() for part in _spoken_story_text(value).split(",") if part.strip()]
    if len(names) < 2:
        return names[0] if names else "the characters"
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])}, and {names[-1]}"


def _finite_framed_action(value: str) -> str:
    """Remove camera framing and turn a participial visual into finite prose."""

    text = _clean(value, maximum=1_200).strip()
    match = re.match(
        r"^(?:tight\s+shot|extreme[- ]?close[- ]?up(?:\s+shot)?|"
        r"reverse[- ]?angle(?:\s+shot)?|tracking(?:\s+shot)?|"
        r"close[- ]?up|medium(?:(?:[- ]wide)|(?:\s+tracking))?\s+shot|wide shot|"
        r"low[- ]?angle(?:\s+shot)?|high[- ]?angle(?:\s+shot)?|"
        r"overhead(?:\s+shot)?|full[- ]?body shot|two[- ]?shot|insert)\s+"
        r"(?:(?:back\s+)?in\s+.+?\s+as\s+|(?:of|on|as)\s+)(.+)$",
        text,
        flags=re.IGNORECASE,
    )
    clause = match.group(1).strip() if match is not None else text

    def plural_subject(subject: str) -> bool:
        last_word = re.sub(r"[^a-z]", "", subject.casefold().split()[-1])
        return (
            " and " in subject.casefold()
            or subject.casefold() in {"we", "they"}
            or last_word
            in {
                "hands",
                "tubes",
                "characters",
                "friends",
                "people",
                "crew",
            }
        )

    # Some visual planners mix a participle with a later finite verb. Put the
    # object first so the result has one clear finite predicate instead of
    # producing prose such as "they climb onto the roof carry the key."
    mixed_climb = re.match(
        r"^(?P<subject>[^,.!?]+?)\s+climbing\s+(?P<path>.+?)\s+"
        r"carry(?:ing)?\s+(?P<object>.+)$",
        clause,
        flags=re.IGNORECASE,
    )
    if mixed_climb is not None:
        subject = mixed_climb.group("subject")
        path = mixed_climb.group("path").strip(" ,.?!")
        carried_object = mixed_climb.group("object").strip(" ,.?!")
        carry = "carry" if plural_subject(subject) else "carries"
        clause = (
            f"{subject} {carry} {carried_object} while climbing {path}"
        )
        if clause and clause[0].islower():
            clause = clause[0].upper() + clause[1:]
        return clause

    # Repair a coordinated participle after an already-finite predicate. The
    # subject determines agreement, so both singular and plural inputs remain
    # grammatical ("the dial glows and comes" / "the lights glow and come").
    finite_lead = re.match(
        r"^(?P<subject>.+?)\s+(?:[A-Za-z'_-]+ly\s+)*"
        r"(?:glow|glows|spark|sparks|light|lights|flicker|flickers|turn|turns)\b",
        clause,
        flags=re.IGNORECASE,
    )
    if finite_lead is not None:
        come = "come" if plural_subject(finite_lead.group("subject")) else "comes"
        clause = re.sub(
            r"\band\s+coming\b",
            f"and {come}",
            clause,
            flags=re.IGNORECASE,
        )

    if match is None and re.match(
        r"^(?:introduce|establish|stage|deliver|resolve|show|hold)\b",
        text,
        flags=re.IGNORECASE,
    ):
        return text
    subject_match = re.match(
        r"^(?P<subject>[^,.!?]+?)\s+"
        r"(?P<adverbs>(?:[A-Za-z'_-]+ly\s+)*)"
        r"(?P<verb>holding|nodding|grabbing|leaning|carrying|standing|walking|running|"
        r"whipping|struggling|fighting|glowing|speaking|exchanging|preparing|hunching|"
        r"polishing|reconnecting|working|adjusting|climbing|coming|hunched)\b"
        r"(?P<tail>.*)$",
        clause,
        flags=re.IGNORECASE,
    )
    if subject_match is None:
        noun_phrase = re.match(
            r"^(?P<subject>(?:the|a|an)\s+.+?)\s+"
            r"(?P<preposition>in|on)\s+(?P<object>.+)$",
            clause,
            flags=re.IGNORECASE,
        )
        if noun_phrase is not None and not re.search(
            r"\b(?:is|are|was|were|rests?|lies?|sits?|stands?|hangs?|waits?)\b",
            noun_phrase.group("subject"),
            flags=re.IGNORECASE,
        ):
            clause = (
                f"{noun_phrase.group('subject')} rests "
                f"{noun_phrase.group('preposition')} {noun_phrase.group('object')}"
            )
        if match is not None and clause and clause[0].islower():
            clause = clause[0].upper() + clause[1:]
        return clause
    subject = subject_match.group("subject")
    plural = plural_subject(subject)
    bases = {
        "holding": "hold",
        "nodding": "nod",
        "grabbing": "grab",
        "leaning": "lean",
        "carrying": "carry",
        "standing": "stand",
        "walking": "walk",
        "running": "run",
        "whipping": "whip",
        "struggling": "struggle",
        "fighting": "fight",
        "glowing": "glow",
        "speaking": "speak",
        "exchanging": "exchange",
        "preparing": "prepare",
        "hunching": "hunch",
        "polishing": "polish",
        "reconnecting": "reconnect",
        "working": "work",
        "adjusting": "adjust",
        "climbing": "climb",
        "coming": "come",
        "hunched": "hunch",
    }

    def finite_verb(base: str) -> str:
        if plural:
            return base
        if base.endswith("y") and len(base) > 1 and base[-2] not in "aeiou":
            return base[:-1] + "ies"
        if base.endswith(("s", "x", "z", "ch", "sh", "o")):
            return base + "es"
        return base + "s"

    base = bases[subject_match.group("verb").casefold()]
    finite = finite_verb(base)
    adverbs = subject_match.group("adverbs") or ""
    clause = f"{subject} {adverbs}{finite}{subject_match.group('tail')}"
    clause = re.sub(
        r"(?:,|\band)\s*(grabbing|holding|carrying|leaning|fighting|speaking|"
        r"exchanging|preparing|polishing|reconnecting|adjusting|climbing|coming)\b",
        lambda match: " and " + finite_verb(bases[match.group(1).casefold()]),
        clause,
        flags=re.IGNORECASE,
    )
    if clause and clause[0].islower():
        clause = clause[0].upper() + clause[1:]
    return clause


def _finite_story_clause(value: Any, *, characters: str) -> str:
    """Turn one subordinate planning clause into declarative story prose."""

    clause = _clean(value, maximum=1_200).strip(" ,;:-")
    if not clause:
        return ""
    subject = characters or "The characters"
    clause = re.sub(
        r"^(?:them|they|the pair|the friends|the characters)\b",
        subject,
        clause,
        count=1,
        flags=re.IGNORECASE,
    )
    gerunds = {
        "choosing": "choose",
        "working": "work",
        "preparing": "prepare",
        "facing": "face",
        "trying": "try",
        "waiting": "wait",
        "moving": "move",
        "holding": "hold",
        "standing": "stand",
        "arguing": "argue",
    }
    for gerund, finite in gerunds.items():
        rewritten = re.sub(
            rf"^{re.escape(subject)}\s+{gerund}\b",
            f"{subject} {finite}",
            clause,
            count=1,
            flags=re.IGNORECASE,
        )
        if rewritten != clause:
            clause = rewritten
            break
    if clause and clause[0].islower():
        clause = clause[0].upper() + clause[1:]
    return clause


def _safe_story_sentence(value: Any, *, fallback: str) -> str:
    """Fail closed when planner instructions survive a natural-language rewrite."""

    sentence = _sentence(value)
    if (
        not sentence
        or _PRODUCTION_DIRECTIVE.search(sentence)
        or _PRODUCTION_META.search(sentence)
    ):
        sentence = _sentence(fallback)
    if _PRODUCTION_DIRECTIVE.search(sentence) or _PRODUCTION_META.search(sentence):
        raise ValueError("audience narration retained production-direction language")
    return sentence


def _natural_story_purpose(
    value: Any,
    *,
    characters: str,
    setting: str,
    fallback: str,
) -> str:
    """Rewrite model-authored production purposes as concise story narration."""

    raw = _spoken_story_text(value, maximum=1_800).strip()
    subject = characters or "The characters"

    framed = _finite_framed_action(raw)
    if framed != raw:
        return _safe_story_sentence(framed, fallback=fallback)

    match = re.match(
        r"^(?:wide shot\s+)?(?:establish(?:es)?|introduc(?:e|es))\s+(.+)$",
        raw,
        re.IGNORECASE,
    )
    if match and not re.search(r"\bintroduc(?:e|es)\b", match.group(1), re.IGNORECASE):
        return _safe_story_sentence(f"We see {match.group(1)}", fallback=fallback)

    if re.match(r"^Establish\b", raw, flags=re.IGNORECASE) and "." in raw:
        _opening, remainder = raw.split(".", 1)
        if remainder.strip():
            return _safe_story_sentence(remainder.strip(), fallback=fallback)

    match = re.match(
        r"^Deliver\s+(?:the\s+)?(?:core\s+)?(?:dramatic\s+)?(?:conflict|turn|beat)\s+"
        r"(?:as|where|with)\s+(.+)$",
        raw,
        flags=re.IGNORECASE,
    )
    if match:
        story = _finite_story_clause(match.group(1), characters=subject)
        return _safe_story_sentence(story, fallback=fallback)

    match = re.match(
        r"^(?:Resolve\s+(?:(?:the|this)\s+)?(?:scene|story|beat|"
        r"(?:dramatic\s+)?(?:tension|conflict))|Show\s+(?:the\s+)?resolution)\s+"
        r"(?:as|where|with|by)\s+(.+)$",
        raw,
        flags=re.IGNORECASE,
    )
    if match:
        story = _finite_story_clause(match.group(1), characters=subject)
        return _safe_story_sentence(story, fallback=fallback)

    if re.match(r"^(?:Introduce|Establish)\b", raw, flags=re.IGNORECASE):
        discovery = re.search(
            r"\band\s+introduce\s+([A-Z][A-Za-z'_-]{0,39})['’]s\s+discovery\s+of\s+(.+)$",
            raw,
            flags=re.IGNORECASE,
        )
        subject_tail = re.search(
            rf"\b{re.escape(subject)}\s+([^.;!?]+)",
            raw,
            flags=re.IGNORECASE,
        )
        if discovery:
            name, discovery_detail = discovery.groups()
            story = (
                f"In {setting}, {name} discovers {discovery_detail}"
                if setting
                else f"{name} discovers {discovery_detail}"
            )
        elif subject_tail:
            story = _finite_story_clause(
                f"{subject} {subject_tail.group(1)}",
                characters=subject,
            )
            if setting and setting.casefold() not in story.casefold():
                story = f"{story} in {setting}"
        elif "under pressure" in raw.casefold():
            story = f"{subject} work under pressure in {setting}"
        else:
            story = fallback
        return _safe_story_sentence(story, fallback=fallback)

    if re.match(r"^(?:Stage|Hold\s+(?:a\s+)?reaction)\b", raw, flags=re.IGNORECASE):
        return _safe_story_sentence(fallback, fallback=fallback)

    match = re.match(r"^Show\s+(.+)$", raw, flags=re.IGNORECASE)
    if match:
        return _safe_story_sentence(f"We see {match.group(1)}", fallback=fallback)

    match = re.match(r"^Reveal\s+(.+)$", raw, flags=re.IGNORECASE)
    if match:
        return _safe_story_sentence(f"We discover {match.group(1)}", fallback=fallback)

    return _safe_story_sentence(raw, fallback=fallback)


def _audience_action(
    action: str,
    *,
    role_occurrence: int,
) -> str:
    """Turn deterministic shot-planning templates into story-pitch prose."""

    establish = _ESTABLISH_ACTION.match(action)
    if establish:
        _setting, characters, purpose = establish.groups()
        spoken_characters = _spoken_characters(characters)
        if spoken_characters.casefold() in {
            "the scene subjects",
            "the characters",
        }:
            return _natural_story_purpose(
                purpose,
                characters="",
                setting="",
                fallback="The story begins.",
            )
        opening = (
            f"{spoken_characters} enter."
            if role_occurrence == 1
            else (
                f"{spoken_characters} face the next turn."
                if " and " in spoken_characters.casefold() or "," in spoken_characters
                else f"{spoken_characters} faces the next turn."
            )
        )
        return _safe_story_sentence(opening, fallback="The story begins.")

    primary = _PRIMARY_ACTION.match(action)
    if primary:
        raw_characters, setting, purpose = primary.groups()
        characters = _spoken_characters(raw_characters)
        fallback = _PRIMARY_OPENINGS[
            min(role_occurrence - 1, len(_PRIMARY_OPENINGS) - 1)
        ].format(characters=characters)
        return _natural_story_purpose(
            purpose,
            characters=characters,
            setting="",
            fallback=fallback,
        )

    bridge = _BRIDGE_ACTION.match(action)
    if bridge:
        setting, _purpose = bridge.groups()
        opening = _BRIDGE_OPENINGS[
            min(role_occurrence - 1, len(_BRIDGE_OPENINGS) - 1)
        ].format(setting=_spoken_story_text(setting))
        return _safe_story_sentence(opening, fallback="The tension carries forward.")

    clean_action = _natural_story_purpose(
        action,
        characters="The characters",
        setting="",
        fallback="The next story beat unfolds.",
    )
    if not clean_action:
        clean_action = "The next story beat unfolds."
    return clean_action


def _lowercase_location_join(value: str) -> str:
    """Join a common-noun story clause grammatically after a spoken location."""

    if re.match(r"^(?:The|A|An|We|They|It|This|That)\b", value):
        return value[0].lower() + value[1:]
    return value


def _normalize_location_label(value: str) -> tuple[str, bool]:
    """Normalize a return label without erasing the planner's time cue."""

    label = _spoken_story_text(value, maximum=240).strip(" ,.;:-")
    is_return = bool(_RETURN_LOCATION.match(label))
    if is_return:
        label = _RETURN_LOCATION.sub("", label).strip(" ,.;:-")
    return label, is_return


def _story_location(
    raw_shot: Mapping[str, Any],
    card: Mapping[str, Any],
    action: str,
    *,
    original_action: str = "",
) -> tuple[str, bool]:
    """Find the audience-facing location and whether it explicitly marks a return."""

    for container in (card, raw_shot):
        for key in ("location", "setting", "scene_setting"):
            value = _spoken_story_text(container.get(key), maximum=240)
            if value:
                return _normalize_location_label(value)
    for candidate in (action, original_action):
        match = _ESTABLISH_ACTION.match(candidate)
        if match:
            return _normalize_location_label(
                _spoken_story_text(match.group(1), maximum=240)
            )
        match = _PRIMARY_ACTION.match(candidate)
        if match:
            return _normalize_location_label(
                _spoken_story_text(match.group(2), maximum=240)
            )
        match = _BRIDGE_ACTION.match(candidate)
        if match:
            return _normalize_location_label(
                _spoken_story_text(match.group(1), maximum=240)
            )
    return "", False


def _directive_for_shot(
    action: str,
    raw_shot: Mapping[str, Any],
    card: Mapping[str, Any],
) -> str:
    """Extract this card's numbered directive from an accidentally broad action."""

    wanted_numbers: list[int] = []
    for container in (raw_shot, card):
        sequence = container.get("sequence")
        if isinstance(sequence, int) and not isinstance(sequence, bool) and sequence > 0:
            wanted_numbers.append(sequence)
        elif isinstance(sequence, str) and sequence.strip().isdigit():
            wanted_numbers.append(int(sequence.strip()))
    shot_match = _SHOT_NUMBER.search(str(raw_shot.get("shot_id") or ""))
    local_shot_number: int | None = None
    if shot_match is not None:
        local_shot_number = int(shot_match.group(1))
        wanted_numbers.append(local_shot_number)
    wanted_numbers = list(dict.fromkeys(wanted_numbers))
    story_scene_number = raw_shot.get("story_scene_number", raw_shot.get("scene_number"))
    markers = list(_SHOT_DIRECTIVE.finditer(action))
    for index, marker in enumerate(markers):
        label = re.sub(r"\s+", "", marker.group("label"))
        if "." in label:
            raw_scene, raw_shot_number = label.split(".", 1)
            matched = (
                isinstance(story_scene_number, int)
                and not isinstance(story_scene_number, bool)
                and int(raw_scene) == story_scene_number
                and local_shot_number == int(raw_shot_number)
            )
        else:
            matched = int(label) in wanted_numbers
        if matched:
            end = markers[index + 1].start() if index + 1 < len(markers) else len(action)
            return _clean(action[marker.end() : end].strip(" ,;:-"), maximum=1_800)
    return ""


def _is_scene_wide_action(action: str) -> bool:
    """Whether a value names several numbered shots instead of one card."""

    return bool(_SCENEWIDE_SHOT_LIST.search(action)) or len(
        _SHOT_DIRECTIVE.findall(action)
    ) > 1


def _card_specific_action(raw_shot: Mapping[str, Any], card: Mapping[str, Any]) -> str:
    """Choose one card's action, never a scene-wide list of several shots."""

    direct_keys = (
        "shot_directive",
        "shot_action",
        "visual_action",
        "source_action",
        "directive",
        "description",
    )
    for container in (card, raw_shot):
        for key in direct_keys:
            candidate = _clean(container.get(key), maximum=1_800)
            extracted = _directive_for_shot(candidate, raw_shot, card)
            if extracted:
                return extracted
            if candidate and not _is_scene_wide_action(candidate):
                return candidate
        source = container.get("source")
        if isinstance(source, Mapping):
            for key in (*direct_keys, "action", "prompt"):
                candidate = _clean(source.get(key), maximum=1_800)
                extracted = _directive_for_shot(candidate, raw_shot, card)
                if extracted:
                    return extracted
                if candidate and not _is_scene_wide_action(candidate):
                    return candidate

    action = _clean(card.get("action") or "", maximum=1_800)
    extracted = _directive_for_shot(action, raw_shot, card)
    if extracted:
        return extracted
    if _is_scene_wide_action(action):
        return "The next story beat unfolds."
    return action or "The next story beat unfolds."


def _omit_repeated_location(narration: str, location: str) -> str:
    """Keep a current-place name from being re-spoken in plan-derived prose."""

    if not location:
        return narration
    escaped = re.escape(location.strip())
    narration = re.sub(
        rf"^In\s+(?:the\s+)?{escaped},\s*",
        "",
        narration,
        flags=re.IGNORECASE,
    )
    narration = re.sub(
        rf"\s+in\s+(?:the\s+)?{escaped}"
        rf"(?=\s+(?:at|by|before|after|during)\b|[,.!?]|$)",
        "",
        narration,
        flags=re.IGNORECASE,
    )
    return narration


def _location_transition(
    location: str,
    *,
    previous_location: str | None,
    seen_locations: set[str],
    explicit_return: bool = False,
) -> tuple[str, str | None]:
    """Speak a location only for the opening card or a genuine move."""

    key = re.sub(r"\s+", " ", location).strip(" ,.;:-")
    key = _LOCATION_TIME_SUFFIX.sub("", key).strip(" ,.;:-").casefold()
    if not key or key == previous_location:
        return "", previous_location
    lead = (
        f"Back in {location},"
        if explicit_return or key in seen_locations
        else f"In {location},"
    )
    seen_locations.add(key)
    return lead, key


def _audience_dialogue(lines: Sequence[str]) -> str:
    sentences: list[str] = []
    for index, line in enumerate(lines):
        clean_line = _clean(line, maximum=600)
        match = re.match(r"^([^:]{1,80}):\s*(.+)$", clean_line)
        if match:
            speaker = _speaker_name(match.group(1))
            if speaker.isupper():
                speaker = speaker.title()
            verb = "says" if index == 0 else "answers"
            sentences.append(f'{speaker} {verb}, "{match.group(2).strip()}"')
        else:
            lead = "We hear" if index == 0 else "The answer comes back"
            sentences.append(f'{lead}, "{clean_line}"')
    return " ".join(_sentence(value, maximum=800) for value in sentences)


def _dialogue_spoken_part(line: str) -> str:
    """Return only the spoken portion of one extracted ``Speaker: line`` value."""

    clean_line = _clean(line, maximum=600)
    match = re.match(r"^[^:]{1,80}:\s*(.+)$", clean_line)
    return match.group(1).strip() if match else clean_line


def _dialogue_match_key(value: Any) -> str:
    """Normalize punctuation without weakening an exact spoken-line match."""

    text = _clean(value, maximum=1_800).casefold()
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _card_explicitly_contains_dialogue(line: str, *card_values: Any) -> bool:
    spoken = _dialogue_match_key(_dialogue_spoken_part(line))
    if not spoken:
        return False
    haystack = _dialogue_match_key(" ".join(_clean(value) for value in card_values))
    return f" {spoken} " in f" {haystack} "


def _explicit_dialogue_assignments(
    shots: Sequence[Any],
    dialogue: Mapping[int, Sequence[str]],
) -> tuple[dict[int, list[str]], set[int]]:
    """Bind an exact source line only to the card that visibly contains it."""

    assignments: dict[int, list[str]] = {}
    explicit_scenes: set[int] = set()
    claimed: dict[int, set[int]] = {}
    for shot_index, raw_shot in enumerate(shots, start=1):
        if not isinstance(raw_shot, Mapping):
            continue
        card = raw_shot.get("storyboard_card")
        if not isinstance(card, Mapping):
            continue
        scene_number = raw_shot.get("story_scene_number", raw_shot.get("scene_number"))
        if isinstance(scene_number, bool) or not isinstance(scene_number, int):
            continue
        action = _card_specific_action(raw_shot, card)
        direction = _clean(card.get("dialogue_or_audio") or "", maximum=1_800)
        for line_index, line in enumerate(dialogue.get(scene_number, ())):
            if line_index in claimed.setdefault(scene_number, set()):
                continue
            if _card_explicitly_contains_dialogue(line, action, direction):
                assignments.setdefault(shot_index, []).append(line)
                claimed[scene_number].add(line_index)
                explicit_scenes.add(scene_number)
    return assignments, explicit_scenes


def _audience_audio(direction: str) -> str:
    # Audio direction remains visible in the production card. Speaking it made
    # short story pitches sound like a technical read-through and could more
    # than triple their requested duration.
    return ""


def _unscoped_dialogue_scene(shots: Sequence[Any]) -> int:
    """Choose the scene that most clearly carries natural-chat dialogue."""

    first_scene: int | None = None
    for raw_shot in shots:
        if not isinstance(raw_shot, Mapping):
            continue
        scene_number = raw_shot.get("story_scene_number", raw_shot.get("scene_number"))
        if isinstance(scene_number, bool) or not isinstance(scene_number, int):
            continue
        if first_scene is None:
            first_scene = scene_number
        card = raw_shot.get("storyboard_card")
        action = _clean(card.get("action") if isinstance(card, Mapping) else "").lower()
        if any(
            marker in action
            for marker in (
                "dialogue",
                "dramatic conflict",
                "reveals",
                "responds",
                "answers",
                "argument",
                "confronts",
            )
        ):
            return scene_number
    return first_scene or 1


def extract_screenplay_dialogue(
    source: str,
    *,
    maximum_scenes: int = 40,
    maximum_lines_per_scene: int = 18,
) -> dict[int, list[str]]:
    """Extract explicit screenplay dialogue without inventing new lines.

    The accepted browser wrappers are treated as plain text.  Only dialogue
    found after an ordinary screenplay scene heading is retained.  Fountain,
    conventional screenplay text, and the bracketed Final Draft text emitted
    by the browser importer are supported.  Quoted prose is a conservative
    fallback when no screenplay-form dialogue was detected in a scene.
    """

    if not isinstance(source, str) or not source.strip():
        return {}
    if not 1 <= maximum_scenes <= 200 or not 1 <= maximum_lines_per_scene <= 100:
        raise ValueError("dialogue extraction bounds are invalid")

    scene_number = 0
    current_speaker: str | None = None
    result: dict[int, list[str]] = {}
    scene_raw_lines: dict[int, list[str]] = {}
    normalised_source = source.replace("\r\n", "\n").replace("\r", "\n")
    # Natural chat often carries several explicit scene beats on one line,
    # for example ``Scene 1: ... Scene 2: ...``.  Split only bounded numbered
    # scene markers so quoted dialogue remains tied to the intended scene.
    normalised_source = _NATURAL_SCENE_MARKER.sub(r"\n\1\n", normalised_source)
    lines = normalised_source.split("\n")

    for raw in lines:
        line = _clean(raw, maximum=1_200)
        fdx_heading = _FDX_SCENE_HEADING.match(line)
        heading = fdx_heading.group(1) if fdx_heading else line
        if _SCENE_HEADING.match(heading) or _NATURAL_SCENE_HEADING.fullmatch(heading):
            scene_number += 1
            current_speaker = None
            if scene_number > maximum_scenes:
                break
            result.setdefault(scene_number, [])
            scene_raw_lines.setdefault(scene_number, [])
            continue
        if scene_number < 1 or scene_number > maximum_scenes:
            continue
        if line:
            scene_raw_lines[scene_number].append(line)

        fdx_character = _FDX_CHARACTER.match(line)
        if fdx_character:
            current_speaker = _speaker_name(fdx_character.group(1)) or None
            continue
        fdx_dialogue = _FDX_DIALOGUE.match(line)
        if fdx_dialogue and current_speaker:
            spoken = _clean(fdx_dialogue.group(1), maximum=600)
            if spoken and len(result[scene_number]) < maximum_lines_per_scene:
                result[scene_number].append(f"{current_speaker}: {spoken}")
            continue

        if not line:
            current_speaker = None
            continue
        if _TRANSITION.match(line):
            current_speaker = None
            continue
        if _CHARACTER_CUE.fullmatch(line) and not line.startswith(("INT", "EXT")):
            current_speaker = _speaker_name(line) or None
            continue
        if current_speaker:
            if line.startswith("(") and line.endswith(")"):
                continue
            if len(result[scene_number]) < maximum_lines_per_scene:
                result[scene_number].append(f"{current_speaker}: {line}")

    for number, raw_lines in scene_raw_lines.items():
        if result.get(number):
            continue
        for raw_line in raw_lines:
            for match in _QUOTED_DIALOGUE.finditer(raw_line):
                spoken = _clean(
                    next(
                        (group for group in match.groups() if group is not None),
                        "",
                    ),
                    maximum=600,
                )
                if spoken and len(result[number]) < maximum_lines_per_scene:
                    result[number].append(spoken)

    if scene_number == 0:
        unscoped: list[str] = []
        for match in _ATTRIBUTED_QUOTED_DIALOGUE.finditer(normalised_source):
            speaker = _speaker_name(match.group(1))
            spoken = _clean(
                next((group for group in match.groups()[1:] if group is not None), ""),
                maximum=600,
            )
            if speaker and spoken and len(unscoped) < maximum_lines_per_scene:
                unscoped.append(f"{speaker}: {spoken}")
        if unscoped:
            result[0] = unscoped

    return {number: values for number, values in result.items() if values}


def build_narrated_pitch_cues(
    brief: Mapping[str, Any],
    timeline: Mapping[str, Any],
    *,
    source_message: str,
) -> list[dict[str, Any]]:
    """Return one audience-facing narration cue for every planned card."""

    shots = timeline.get("shots")
    if not isinstance(shots, Sequence) or isinstance(shots, (str, bytes)) or not shots:
        raise ValueError("a non-empty planned shot timeline is required")
    dialogue = extract_screenplay_dialogue(source_message)
    unscoped_dialogue = dialogue.pop(0, [])
    if unscoped_dialogue:
        target_scene = _unscoped_dialogue_scene(shots)
        dialogue[target_scene] = [
            *dialogue.get(target_scene, []),
            *unscoped_dialogue,
        ]
    explicit_dialogue, explicit_dialogue_scenes = _explicit_dialogue_assignments(
        shots,
        dialogue,
    )
    dialogue_offsets: dict[int, int] = {}
    role_counts: dict[str, int] = {}
    heard_audio: set[str] = set()
    seen_locations: set[str] = set()
    previous_location: str | None = None
    cues: list[dict[str, Any]] = []
    primary_dialogue_scenes = {
        shot.get("story_scene_number", shot.get("scene_number"))
        for shot in shots
        if isinstance(shot, Mapping)
        and _clean(shot.get("role")).lower().replace(" ", "_").replace("-", "_")
        == "primary_coverage"
    }

    for index, raw_shot in enumerate(shots, start=1):
        if not isinstance(raw_shot, Mapping):
            raise ValueError("each planned shot must be an object")
        card = raw_shot.get("storyboard_card")
        if not isinstance(card, Mapping):
            raise ValueError("each planned shot must include a storyboard card")
        scene_number = raw_shot.get("story_scene_number", raw_shot.get("scene_number"))
        if isinstance(scene_number, bool) or not isinstance(scene_number, int):
            raise ValueError("each planned shot must include an integer scene number")

        raw_role = _clean(raw_shot.get("role") or "planned_shot", maximum=120)
        role_key = raw_role.lower().replace(" ", "_").replace("-", "_")
        role = raw_role.replace("_", " ")
        role_counts[role_key] = role_counts.get(role_key, 0) + 1
        original_action = _clean(card.get("action") or "", maximum=1_800)
        action = _card_specific_action(raw_shot, card)
        direction = _clean(card.get("dialogue_or_audio") or "")
        available_lines = dialogue.get(scene_number, [])
        offset = dialogue_offsets.get(scene_number, 0)
        dialogue_card = (
            role_key == "primary_coverage"
            or scene_number not in primary_dialogue_scenes
        )
        selected_lines = explicit_dialogue.get(index, [])
        if not selected_lines and scene_number not in explicit_dialogue_scenes:
            selected_lines = available_lines[offset : offset + 2] if dialogue_card else []
        if selected_lines:
            if scene_number not in explicit_dialogue_scenes:
                dialogue_offsets[scene_number] = offset + len(selected_lines)
            dialogue_text = _audience_dialogue(selected_lines)
            dialogue_source = "source_exact"
        else:
            dialogue_text = ""
            dialogue_source = "planned_direction"

        audio_text = _audience_audio(direction)
        audio_key = audio_text.casefold()
        if audio_key in heard_audio:
            audio_text = ""
        elif audio_key:
            heard_audio.add(audio_key)

        story_text = _audience_action(
            action,
            role_occurrence=role_counts[role_key],
        )
        location, explicit_return = _story_location(
            raw_shot,
            card,
            action,
            original_action=original_action,
        )
        location_lead, previous_location = _location_transition(
            location,
            previous_location=previous_location,
            seen_locations=seen_locations,
            explicit_return=explicit_return,
        )
        story_text = _omit_repeated_location(story_text, location)
        if location_lead:
            story_text = _lowercase_location_join(story_text)
        narration_parts = (
            [location_lead, dialogue_text]
            if dialogue_text
            else [location_lead, story_text, audio_text]
        )
        narration = _clean(
            " ".join(part for part in narration_parts if part),
            maximum=3_600,
        )
        if not dialogue_text:
            narration = _spoken_story_text(narration, maximum=3_600)
            if _PRODUCTION_DIRECTIVE.search(narration) or _PRODUCTION_META.search(narration):
                raise ValueError("audience narration retained production-direction language")
        if not narration:
            raise ValueError("an audience-facing narration cue could not be built")
        cues.append(
            {
                "sequence": index,
                "shot_id": _clean(raw_shot.get("shot_id") or f"CARD-{index:03d}", maximum=120),
                "scene_number": scene_number,
                "role": role,
                "planned_in_timecode": _clean(
                    raw_shot.get("planned_in_timecode") or "--:--:--:--", maximum=32
                ),
                "planned_out_timecode": _clean(
                    raw_shot.get("planned_out_timecode") or "--:--:--:--", maximum=32
                ),
                "action": action,
                "dialogue_or_audio": direction,
                "dialogue_lines": selected_lines,
                "dialogue_source": dialogue_source,
                "narration": narration,
            }
        )

    if len(cues) != len(shots):
        raise ValueError("narrated pitch cue coverage is incomplete")
    return cues


def pitch_narration_text(
    brief: Mapping[str, Any],
    cues: Sequence[Mapping[str, Any]],
) -> str:
    """Build the complete human-readable narration document used by the MP4."""

    title = _clean(brief.get("title") or "Untitled production", maximum=160)
    summary = _clean(brief.get("summary") or "No synopsis supplied.", maximum=1_200)
    lines = [
        f"{title} — NARRATED STORYBOARD PITCH",
        "PREVISUALIZATION — STORYBOARD ART IS NOT FINAL FOOTAGE",
        f"Synopsis: {summary}",
        f"Complete cue coverage: {len(cues)} cards.",
        "",
    ]
    for cue in cues:
        lines.extend(
            [
                f"CARD {cue['sequence']} — {cue['shot_id']} — "
                f"{cue['planned_in_timecode']} to {cue['planned_out_timecode']}",
                f"VISUAL: {cue['action']}",
                f"NARRATION: {cue['narration']}",
                f"DIALOGUE SOURCE: {cue['dialogue_source']}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "build_narrated_pitch_cues",
    "extract_screenplay_dialogue",
    "pitch_narration_text",
]
