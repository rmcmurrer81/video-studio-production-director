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


_SHOT_ID = re.compile(r"\bSC\d{1,3}-SH\d{1,3}\b", re.IGNORECASE)
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

_ESTABLISH_OPENINGS = (
    "We begin in {setting}, with {characters} together.",
    "The story moves to {setting}, where {characters} face the next turn.",
    "Later, we arrive in {setting} as {characters} move forward.",
)
_PRIMARY_OPENINGS = (
    "The focus shifts to {characters} in {setting}.",
    "Now {characters} take the lead in {setting}.",
    "Here, {characters} carry the moment in {setting}.",
)
_BRIDGE_OPENINGS = (
    "A closer detail in {setting} lets the moment sink in.",
    "One revealing detail in {setting} carries the tension forward.",
    "The moment lingers on a telling detail in {setting}.",
)
_GENERIC_OPENINGS = (
    "We begin as",
    "Next,",
    "From there,",
    "The story continues as",
    "Finally,",
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


def _story_purpose(value: Any) -> str:
    purpose = _clean(value)
    purpose = re.sub(r"^Establish\s+", "We learn ", purpose, flags=re.IGNORECASE)
    purpose = re.sub(r"^Show\s+", "We see ", purpose, flags=re.IGNORECASE)
    purpose = re.sub(r"^Reveal\s+", "We discover ", purpose, flags=re.IGNORECASE)
    return _sentence(purpose)


def _audience_action(
    action: str,
    *,
    role_occurrence: int,
    sequence: int,
) -> str:
    """Turn deterministic shot-planning templates into story-pitch prose."""

    establish = _ESTABLISH_ACTION.match(action)
    if establish:
        setting, characters, purpose = establish.groups()
        opening = _ESTABLISH_OPENINGS[
            min(role_occurrence - 1, len(_ESTABLISH_OPENINGS) - 1)
        ].format(
            setting=_spoken_story_text(setting),
            characters=_spoken_characters(characters),
        )
        return f"{opening} {_story_purpose(purpose)}".strip()

    primary = _PRIMARY_ACTION.match(action)
    if primary:
        characters, setting, purpose = primary.groups()
        opening = _PRIMARY_OPENINGS[
            min(role_occurrence - 1, len(_PRIMARY_OPENINGS) - 1)
        ].format(
            setting=_spoken_story_text(setting),
            characters=_spoken_characters(characters),
        )
        return f"{opening} {_story_purpose(purpose)}".strip()

    bridge = _BRIDGE_ACTION.match(action)
    if bridge:
        setting, purpose = bridge.groups()
        opening = _BRIDGE_OPENINGS[
            min(role_occurrence - 1, len(_BRIDGE_OPENINGS) - 1)
        ].format(setting=_spoken_story_text(setting))
        return f"{opening} {_story_purpose(purpose)}".strip()

    opening = _GENERIC_OPENINGS[min(sequence - 1, len(_GENERIC_OPENINGS) - 1)]
    clean_action = _sentence(action)
    if not clean_action:
        clean_action = "The next story beat unfolds."
    if opening.endswith((",", " as")):
        clean_action = clean_action[0].lower() + clean_action[1:]
    return f"{opening} {clean_action}".strip()


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


def _audience_audio(direction: str) -> str:
    match = _BRIEF_AUDIO.search(direction)
    if not match:
        return ""
    audio = _sentence(match.group(1), maximum=900)
    return f"Around them, {audio[0].lower() + audio[1:]}" if audio else ""


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
    dialogue_offsets: dict[int, int] = {}
    role_counts: dict[str, int] = {}
    heard_audio: set[str] = set()
    cues: list[dict[str, Any]] = []

    for index, raw_shot in enumerate(shots, start=1):
        if not isinstance(raw_shot, Mapping):
            raise ValueError("each planned shot must be an object")
        card = raw_shot.get("storyboard_card")
        if not isinstance(card, Mapping):
            raise ValueError("each planned shot must include a storyboard card")
        scene_number = raw_shot.get("scene_number")
        if isinstance(scene_number, bool) or not isinstance(scene_number, int):
            raise ValueError("each planned shot must include an integer scene number")

        raw_role = _clean(raw_shot.get("role") or "planned_shot", maximum=120)
        role_key = raw_role.lower().replace(" ", "_").replace("-", "_")
        role = raw_role.replace("_", " ")
        role_counts[role_key] = role_counts.get(role_key, 0) + 1
        action = _clean(card.get("action") or "The next story beat unfolds.")
        direction = _clean(card.get("dialogue_or_audio") or "")
        available_lines = dialogue.get(scene_number, [])
        offset = dialogue_offsets.get(scene_number, 0)
        selected_lines = available_lines[offset : offset + 2]
        if selected_lines:
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

        narration_parts = [
            _audience_action(
                action,
                role_occurrence=role_counts[role_key],
                sequence=index,
            ),
            dialogue_text,
            audio_text,
        ]
        narration = _clean(
            " ".join(part for part in narration_parts if part),
            maximum=3_600,
        )
        narration = _spoken_story_text(narration, maximum=3_600)
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
