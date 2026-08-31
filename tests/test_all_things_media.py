from __future__ import annotations

import re
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
        self.assertEqual(cues[0]["dialogue_source"], "planned_direction")
        self.assertEqual(cues[0]["dialogue_lines"], [])
        self.assertEqual(cues[1]["dialogue_source"], "source_exact")
        self.assertEqual(len(cues[1]["dialogue_lines"]), 2)
        self.assertIn('Mara says, "Battery C', cues[1]["narration"])
        narration = pitch_narration_text(brief, cues)
        self.assertIn("Complete cue coverage: 2 cards.", narration)
        self.assertIn("CARD 2 — SC01-SH02", narration)

    def test_pitch_narration_uses_story_language_not_internal_card_labels(self) -> None:
        brief = {"title": "The Last Jump", "summary": "Two friends risk one final jump."}
        purpose_one = (
            "Establish the critical stakes and the failing state of Battery C "
            "through dialogue between Mara and Jonah."
        )
        purpose_two = (
            "Show the rising warning lights, Mara holding the silver compass, "
            "and their mutual decision to initiate the jump together."
        )
        shots = []
        for scene_number, setting, purpose in (
            (1, "Quiet orbital repair bay", purpose_one),
            (2, "Orbital repair bay with rising warning lights", purpose_two),
        ):
            for shot_number, role, action in (
                (
                    1,
                    "establishing",
                    f"Establish {setting} and the spatial relationship of Mara, Jonah "
                    f"before this scene beat: {purpose}",
                ),
                (
                    2,
                    "primary_coverage",
                    f"Stage Mara, Jonah in {setting} for the primary scene beat: {purpose}",
                ),
                (
                    3,
                    "continuity_bridge",
                    "Hold a reaction, prop, or environmental detail in "
                    f"{setting} that directly supports this beat: {purpose}",
                ),
            ):
                shots.append(
                    {
                        "shot_id": f"SC{scene_number:02d}-SH{shot_number:02d}",
                        "scene_number": scene_number,
                        "role": role,
                        "planned_in_timecode": "00:00:00:00",
                        "planned_out_timecode": "00:00:03:00",
                        "storyboard_card": {
                            "action": action,
                            "dialogue_or_audio": (
                                "Protect canonical primary coverage and clean establishing "
                                "ambience. Brief audio direction: Low ship hum and warning alarm."
                            ),
                        },
                    }
                )

        source = (
            "Scene 1: Mara says, ‘Battery C will not survive another jump.’ "
            "Jonah answers, ‘Then we make this one count.’ Scene 2: The warning rises."
        )
        cues = build_narrated_pitch_cues(brief, {"shots": shots}, source_message=source)

        self.assertEqual([cue["role"] for cue in cues], [
            "establishing",
            "primary coverage",
            "continuity bridge",
            "establishing",
            "primary coverage",
            "continuity bridge",
        ])
        forbidden = re.compile(
            r"\b(?:canon(?:ical)?|establish(?:ing)?|primary coverage|"
            r"continuity bridge|card\s+\d+|SC\d{2}-SH\d{2})\b",
            re.IGNORECASE,
        )
        for cue in cues:
            self.assertIsNone(forbidden.search(cue["narration"]), cue["narration"])
        openings = [cue["narration"].split(".", 1)[0] for cue in cues]
        self.assertEqual(len(set(openings)), len(openings), openings)
        self.assertTrue(cues[0]["narration"].startswith("In Quiet orbital repair bay, Mara and Jonah enter"))
        self.assertIn("A reaction carries", cues[-1]["narration"])
        self.assertIn("Battery C will not survive another jump.", cues[1]["narration"])
        self.assertEqual(
            sum("low ship hum and warning alarm" in cue["narration"].lower() for cue in cues),
            0,
        )

    def test_natural_chat_quotes_drive_concise_conflict_coverage(self) -> None:
        source = (
            "Create a 45-second science-fiction dialogue scene in a quiet orbital repair bay. "
            "Two old friends, Mara and Dax, must decide whether to leave a damaged station "
            "before an alien signal reaches them. Keep the room tone subtle and the dialogue "
            "clear. Mara says, “The signal knows our names.” "
            "Dax answers, “Then we leave before it learns our plans.” "
            "End with them choosing to work together. Make this as a storyboard production "
            "plan and narrated investor-pitch preview, not finished filmed footage."
        )
        self.assertEqual(
            extract_screenplay_dialogue(source),
            {
                0: [
                    "Mara: The signal knows our names.",
                    "Dax: Then we leave before it learns our plans.",
                ]
            },
        )
        purposes = (
            "Introduce the quiet, tense atmosphere of the orbital repair bay and introduce "
            "Mara's discovery of the approaching signal.",
            "Deliver the core dramatic conflict as Mara reveals the signal's nature and "
            "Dax responds with determination.",
            "Resolve the tension with Mara and Dax choosing to work together to escape the "
            "station before the signal arrives.",
        )
        settings = (
            "Orbital Repair Bay, Damaged Space Station",
            "Orbital Repair Bay, Console Area",
            "Orbital Repair Bay, Hangar Controls",
        )
        shots = []
        for scene_number, (purpose, setting) in enumerate(zip(purposes, settings), start=1):
            for shot_number, role in enumerate(
                ("establishing", "primary_coverage", "continuity_bridge"),
                start=1,
            ):
                if role == "establishing":
                    action = (
                        f"Establish {setting} and the spatial relationship of Mara, Dax "
                        f"before this scene beat: {purpose}"
                    )
                elif role == "primary_coverage":
                    action = (
                        f"Stage Mara, Dax in {setting} for the primary scene beat: {purpose}"
                    )
                else:
                    action = (
                        f"Hold a reaction, prop, or environmental detail in {setting} "
                        f"that directly supports this beat: {purpose}"
                    )
                shots.append(
                    {
                        "shot_id": f"SC{scene_number:02d}-SH{shot_number:02d}",
                        "scene_number": scene_number,
                        "role": role,
                        "planned_in_timecode": "00:00:00:00",
                        "planned_out_timecode": "00:00:05:00",
                        "storyboard_card": {
                            "action": action,
                            "dialogue_or_audio": (
                                "Brief audio direction: long technical audio notes stay in "
                                "the production document instead of the spoken pitch."
                            ),
                        },
                    }
                )

        cues = build_narrated_pitch_cues(
            {"title": "Signal Horizon", "summary": "Two friends choose to leave."},
            {"shots": shots},
            source_message=source,
        )
        exact = [cue for cue in cues if cue["dialogue_source"] == "source_exact"]
        self.assertEqual(len(exact), 1)
        self.assertEqual(exact[0]["shot_id"], "SC02-SH02")
        self.assertEqual(
            exact[0]["dialogue_lines"],
            [
                "Mara: The signal knows our names.",
                "Dax: Then we leave before it learns our plans.",
            ],
        )
        self.assertIn("The signal knows our names.", exact[0]["narration"])
        self.assertIn("Then we leave before it learns our plans.", exact[0]["narration"])
        self.assertEqual(
            cues[1]["narration"],
            "Mara discovers the approaching signal.",
        )
        self.assertEqual(
            cues[7]["narration"],
            "Mara and Dax choose to work together to escape the station before the signal arrives.",
        )
        production_language = re.compile(
            r"\b(?:introduc(?:e|es|ed|ing)|establish(?:es|ed|ing)?|"
            r"stag(?:e|es|ed|ing)|deliver(?:s|ed|ing)?|resolve the scene|"
            r"show the resolution|hold a reaction|scene beat|primary coverage|"
            r"continuity bridge|brief audio direction)\b",
            re.IGNORECASE,
        )
        for cue in cues:
            if cue["dialogue_source"] != "source_exact":
                self.assertIsNone(
                    production_language.search(cue["narration"]),
                    cue["narration"],
                )
        spoken = " ".join(cue["narration"] for cue in cues)
        self.assertNotRegex(
            spoken.lower(),
            r"we learn|deliver the core dramatic conflict|show the resolution|"
            r"brief audio direction|primary coverage|continuity bridge",
        )
        self.assertLessEqual(len(spoken.split()), 95)

    def test_rewrites_only_bounded_resolution_planner_variants(self) -> None:
        cases = (
            (
                "Resolve this tension by Mara and Dax choosing to trust one another",
                "Mara and Dax choose to trust one another.",
            ),
            (
                "Resolve the dramatic conflict as Mara and Dax facing the signal together",
                "Mara and Dax face the signal together.",
            ),
            (
                "Resolve conflict where Mara and Dax preparing the station for departure",
                "Mara and Dax prepare the station for departure.",
            ),
            (
                "Resolve the encrypted signal with Mara and Dax",
                "Resolve the encrypted signal with Mara and Dax.",
            ),
        )
        shots = []
        for sequence, (purpose, _expected) in enumerate(cases, start=1):
            shots.append(
                {
                    "shot_id": f"SC{sequence:02d}-SH01",
                    "scene_number": sequence,
                    "role": "primary_coverage",
                    "planned_in_timecode": "00:00:00:00",
                    "planned_out_timecode": "00:00:05:00",
                    "storyboard_card": {
                        "action": (
                            "Stage Mara, Dax in the orbital repair bay for the primary "
                            f"scene beat: {purpose}"
                        ),
                        "dialogue_or_audio": "Low room tone.",
                    },
                }
            )

        cues = build_narrated_pitch_cues(
            {"title": "Signal Horizon", "summary": "Two friends choose to leave."},
            {"shots": shots},
            source_message="",
        )

        self.assertEqual(
            [cue["narration"] for cue in cues],
            [
                "In the orbital repair bay, " + cases[0][1],
                *[expected for _purpose, expected in cases[1:]],
            ],
        )

    def test_quoted_prose_is_used_only_as_a_fallback(self) -> None:
        source = """EXT. BEACH - SUNSET
Elena watches the tide. “We can still turn back,” she says.
"""
        self.assertEqual(
            extract_screenplay_dialogue(source),
            {1: ["We can still turn back,"]},
        )

    def test_extracts_curly_quoted_dialogue_from_inline_natural_chat_scenes(self) -> None:
        source = (
            "Create a complete dialogue plan. "
            "Scene 1: Mara tells Jonah, ‘Battery C will not survive another jump.’ "
            "Jonah answers, ‘Then we make this one count.’ "
            "Scene 2: Warning lights rise as Mara grips the silver compass."
        )
        self.assertEqual(
            extract_screenplay_dialogue(source),
            {
                1: [
                    "Battery C will not survive another jump.",
                    "Then we make this one count.",
                ]
            },
        )

        timeline = {
            "shots": [
                {
                    "shot_id": "SC01-SH01",
                    "scene_number": 1,
                    "role": "establishing",
                    "planned_in_timecode": "00:00:00:00",
                    "planned_out_timecode": "00:00:03:00",
                    "storyboard_card": {
                        "action": "Mara and Jonah inspect Battery C.",
                        "dialogue_or_audio": "Protect the exchange.",
                    },
                },
                {
                    "shot_id": "SC02-SH01",
                    "scene_number": 2,
                    "role": "establishing",
                    "planned_in_timecode": "00:00:03:00",
                    "planned_out_timecode": "00:00:06:00",
                    "storyboard_card": {
                        "action": "Mara grips the silver compass.",
                        "dialogue_or_audio": "Warning chime and room tone.",
                    },
                },
            ]
        }
        cues = build_narrated_pitch_cues(
            {"title": "The Last Jump", "summary": "They choose to leave."},
            timeline,
            source_message=source,
        )
        self.assertEqual(cues[0]["dialogue_source"], "source_exact")
        self.assertIn(
            "Battery C will not survive another jump.",
            cues[0]["narration"],
        )
        self.assertIn("Then we make this one count.", cues[0]["narration"])
        self.assertEqual(cues[1]["dialogue_source"], "planned_direction")


    def test_speaks_locations_only_on_moves_and_marks_returns(self) -> None:
        locations = ("Repair Bay", "Repair Bay", "Observation Ring", "Observation Ring", "Repair Bay")
        shots = []
        for sequence, location in enumerate(locations, start=1):
            shots.append(
                {
                    "shot_id": f"SC01-SH{sequence:02d}",
                    "scene_number": 1,
                    "role": "primary_coverage",
                    "planned_in_timecode": "00:00:00:00",
                    "planned_out_timecode": "00:00:03:00",
                    "storyboard_card": {
                        "setting": location,
                        "action": f"Reveal the next decision in {location}.",
                        "dialogue_or_audio": "Room tone.",
                    },
                }
            )

        cues = build_narrated_pitch_cues({}, {"shots": shots}, source_message="")

        self.assertEqual([cue["sequence"] for cue in cues], [1, 2, 3, 4, 5])
        self.assertTrue(cues[0]["narration"].startswith("In Repair Bay,"))
        self.assertNotIn("Repair Bay", cues[1]["narration"])
        self.assertTrue(cues[2]["narration"].startswith("In Observation Ring,"))
        self.assertNotIn("Observation Ring", cues[3]["narration"])
        self.assertTrue(cues[4]["narration"].startswith("Back in Repair Bay,"))

    def test_location_lead_does_not_duplicate_bridge_setting(self) -> None:
        shots = [
            {
                "shot_id": "SC01-SH01",
                "scene_number": 1,
                "role": "continuity_bridge",
                "planned_in_timecode": "00:00:00:00",
                "planned_out_timecode": "00:00:03:00",
                "storyboard_card": {
                    "action": (
                        "Hold a reaction, prop, or environmental detail in Observation Ring "
                        "that directly supports this beat: the signal changes."
                    ),
                    "dialogue_or_audio": "Room tone.",
                },
            }
        ]

        cue = build_narrated_pitch_cues({}, {"shots": shots}, source_message="")[0]

        self.assertTrue(cue["narration"].startswith("In Observation Ring,"))
        self.assertEqual(cue["narration"].casefold().count("observation ring"), 1)


    def test_normalizes_explicit_back_in_location_labels(self) -> None:
        shots = []
        for number, setting in enumerate(
            (
                "HARBOR RADIO WORKSHOP",
                "OBSERVATION DECK",
                "BACK IN HARBOR RADIO WORKSHOP, MOMENTS LATER",
            ),
            start=1,
        ):
            shots.append(
                {
                    "shot_id": f"SC01-SH{number:02d}",
                    "scene_number": 1,
                    "role": "primary_coverage",
                    "planned_in_timecode": "00:00:00:00",
                    "planned_out_timecode": "00:00:03:00",
                    "storyboard_card": {
                        "setting": setting,
                        "action": "Reveal the next turn in the transmission.",
                        "dialogue_or_audio": "Room tone.",
                    },
                }
            )

        cues = build_narrated_pitch_cues({}, {"shots": shots}, source_message="")

        self.assertTrue(
            cues[2]["narration"].startswith(
                "Back in HARBOR RADIO WORKSHOP, MOMENTS LATER,"
            )
        )
        self.assertNotIn("In BACK IN", cues[2]["narration"])

    def test_uses_global_sequence_for_live_scene_wide_shot_actions(self) -> None:
        scene_wide_action = (
            "Shot 4: Nora locks the frequency dial. "
            "Shot 5: The signal paints a warning across the window. "
            "Shot 6: A flare crosses the stormwater."
        )
        templated_action = (
            "Stage Nora in BACK IN HARBOR RADIO WORKSHOP, MOMENTS LATER "
            f"for the primary scene beat: {scene_wide_action}"
        )
        shots = [
            {
                "shot_id": "SC02-SH01",
                "sequence": 4,
                "scene_number": 2,
                "role": "primary_coverage",
                "planned_in_timecode": "00:00:00:00",
                "planned_out_timecode": "00:00:03:00",
                "storyboard_card": {
                    "action": templated_action,
                    "shot_directive": scene_wide_action,
                    "dialogue_or_audio": "Room tone.",
                },
            },
            {
                "shot_id": "SC02-SH02",
                "sequence": 5,
                "scene_number": 2,
                "role": "primary_coverage",
                "planned_in_timecode": "00:00:03:00",
                "planned_out_timecode": "00:00:06:00",
                "source": {"action": scene_wide_action},
                "storyboard_card": {
                    "action": templated_action,
                    "dialogue_or_audio": "Room tone.",
                },
            },
            {
                "shot_id": "SC02-SH03",
                "sequence": 6,
                "scene_number": 2,
                "role": "primary_coverage",
                "planned_in_timecode": "00:00:06:00",
                "planned_out_timecode": "00:00:09:00",
                "storyboard_card": {
                    "action": templated_action,
                    "dialogue_or_audio": "Room tone.",
                },
            },
        ]

        cues = build_narrated_pitch_cues({}, {"shots": shots}, source_message="")

        self.assertEqual(
            [cue["action"] for cue in cues],
            [
                "Nora locks the frequency dial.",
                "The signal paints a warning across the window.",
                "A flare crosses the stormwater.",
            ],
        )
        for cue, own_detail in zip(
            cues,
            ("frequency dial", "warning across the window", "flare crosses"),
        ):
            self.assertIn(own_detail, cue["narration"].casefold())
            self.assertNotRegex(cue["narration"], r"\bShot\s+[456]\b")
        self.assertNotIn("warning across the window", cues[0]["narration"].casefold())
        self.assertNotIn("stormwater", cues[1]["narration"].casefold())
        self.assertNotIn("frequency dial", cues[2]["narration"].casefold())
        self.assertTrue(
            cues[0]["narration"].startswith(
                "Back in HARBOR RADIO WORKSHOP, MOMENTS LATER,"
            )
        )
        self.assertEqual(
            sum(cue["narration"].startswith("Back in ") for cue in cues),
            1,
        )

    def test_falls_back_to_local_shot_number_after_global_sequence(self) -> None:
        cue = build_narrated_pitch_cues(
            {},
            {
                "shots": [
                    {
                        "shot_id": "SC02-SH01",
                        "sequence": 4,
                        "scene_number": 2,
                        "role": "primary_coverage",
                        "planned_in_timecode": "00:00:00:00",
                        "planned_out_timecode": "00:00:03:00",
                        "storyboard_card": {
                            "action": (
                                "Shot 1: Nora closes the relay. "
                                "Shot 2: The warning lamp flickers. "
                                "Shot 3: Rain washes the window."
                            ),
                            "dialogue_or_audio": "Room tone.",
                        },
                    }
                ]
            },
            source_message="",
        )[0]

        self.assertEqual(cue["action"], "Nora closes the relay.")
        self.assertIn("closes the relay", cue["narration"].casefold())
        self.assertNotRegex(cue["narration"], r"\bShot\s+[123]\b")

if __name__ == "__main__":
    unittest.main()
