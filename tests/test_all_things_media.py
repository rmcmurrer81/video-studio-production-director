from __future__ import annotations

import unittest

from kira_studio.all_things_media import (
    build_narrated_pitch_cues,
    extract_screenplay_dialogue,
    pitch_narration_text,
)


class AllThingsMediaTests(unittest.TestCase):
    def test_extracts_explicit_screenplay_dialogue_by_scene(self) -> None:
        source = """Untrusted wrapper text.

INT. REPAIR BAY - NIGHT

MARA
(quietly)
Battery C will not survive another cycle.

ILAN
Then we leave before the doors seal.

EXT. OBSERVATION RING - LATER

MARA (V.O.)
I can still see Earth from here.
"""
        self.assertEqual(
            extract_screenplay_dialogue(source),
            {
                1: [
                    "MARA: Battery C will not survive another cycle.",
                    "ILAN: Then we leave before the doors seal.",
                ],
                2: ["MARA: I can still see Earth from here."],
            },
        )

    def test_supports_bracketed_final_draft_text(self) -> None:
        source = """[Scene Heading] INT. CONTROL ROOM - DAY
[Character] KIRA
[Dialogue] We only get one clean attempt.
[Character] ROBERT
[Dialogue] Then let us make it count.
"""
        self.assertEqual(
            extract_screenplay_dialogue(source),
            {1: ["KIRA: We only get one clean attempt.", "ROBERT: Then let us make it count."]},
        )

    def test_builds_one_complete_cue_per_card_and_uses_exact_lines(self) -> None:
        brief = {"title": "The Orbital Threshold", "summary": "Two friends choose whether to stay."}
        timeline = {
            "shots": [
                {
                    "shot_id": "SC01-SH01",
                    "scene_number": 1,
                    "role": "establishing",
                    "planned_in_timecode": "00:00:00:00",
                    "planned_out_timecode": "00:00:04:00",
                    "storyboard_card": {
                        "action": "Reveal the damaged repair bay.",
                        "dialogue_or_audio": "Low alarm and room tone.",
                    },
                },
                {
                    "shot_id": "SC01-SH02",
                    "scene_number": 1,
                    "role": "primary_coverage",
                    "planned_in_timecode": "00:00:04:00",
                    "planned_out_timecode": "00:00:09:00",
                    "storyboard_card": {
                        "action": "Mara confronts Ilan beside Battery C.",
                        "dialogue_or_audio": "Protect the argument.",
                    },
                },
            ]
        }
        source = """INT. REPAIR BAY - NIGHT

MARA
Battery C will not survive another cycle.

ILAN
Then we leave before the doors seal.
"""
        cues = build_narrated_pitch_cues(brief, timeline, source_message=source)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0]["dialogue_source"], "source_exact")
        self.assertEqual(len(cues[0]["dialogue_lines"]), 2)
        self.assertIn("MARA: Battery C", cues[0]["narration"])
        self.assertEqual(cues[1]["dialogue_source"], "planned_direction")
        narration = pitch_narration_text(brief, cues)
        self.assertIn("Complete cue coverage: 2 cards.", narration)
        self.assertIn("CARD 2 — SC01-SH02", narration)

    def test_quoted_prose_is_used_only_as_a_fallback(self) -> None:
        source = """EXT. BEACH - SUNSET
Elena watches the tide. “We can still turn back,” she says.
"""
        self.assertEqual(
            extract_screenplay_dialogue(source),
            {1: ["We can still turn back,"]},
        )


if __name__ == "__main__":
    unittest.main()
