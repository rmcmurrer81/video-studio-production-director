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
_QUOTED_DIALOGUE = re.compile(r"[\"“]([^\"”\n]{2,500})[\"”]")


def _clean(value: Any, *, maximum: int = 1_800) -> str:
    text = str(value or "").replace("\x00", " ")
    text = re.sub(r"[\t ]+", " ", text)
    return text.strip()[:maximum]


def _speaker_name(value: str) -> str:
    speaker = re.sub(r"\s*\([^)]*\)\s*$", "", value).strip()
    return _clean(speaker, maximum=80)


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
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    for raw in lines:
        line = _clean(raw, maximum=1_200)
        fdx_heading = _FDX_SCENE_HEADING.match(line)
        heading = fdx_heading.group(1) if fdx_heading else line
        if _SCENE_HEADING.match(heading):
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
                spoken = _clean(match.group(1), maximum=600)
                if spoken and len(result[number]) < maximum_lines_per_scene:
                    result[number].append(spoken)

    return {number: values for number, values in result.items() if values}


def build_narrated_pitch_cues(
    brief: Mapping[str, Any],
    timeline: Mapping[str, Any],
    *,
    source_message: str,
) -> list[dict[str, Any]]:
    """Return one complete narration cue for every planned storyboard card."""

    shots = timeline.get("shots")
    if not isinstance(shots, Sequence) or isinstance(shots, (str, bytes)) or not shots:
        raise ValueError("a non-empty planned shot timeline is required")
    dialogue = extract_screenplay_dialogue(source_message)
    dialogue_offsets: dict[int, int] = {}
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

        role = _clean(raw_shot.get("role") or "planned shot", maximum=120).replace("_", " ")
        action = _clean(card.get("action") or "The planned action is not specified.")
        direction = _clean(
            card.get("dialogue_or_audio")
            or "No dialogue or audio direction is supplied for this card."
        )
        available_lines = dialogue.get(scene_number, [])
        offset = dialogue_offsets.get(scene_number, 0)
        selected_lines = available_lines[offset : offset + 2]
        if selected_lines:
            dialogue_offsets[scene_number] = offset + len(selected_lines)
            spoken = " Explicit script line. " + " Next line. ".join(selected_lines)
            dialogue_source = "source_exact"
        else:
            spoken = f" Dialogue and audio direction: {direction}"
            dialogue_source = "planned_direction"
        narration = _clean(
            f"Card {index}, {role}. {action}.{spoken}",
            maximum=3_600,
        )
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
