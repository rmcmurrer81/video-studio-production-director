from __future__ import annotations

import re
import unittest

from kira_studio.all_things_agentic import ProductionBrief, compile_storyboard_timeline
from kira_studio.all_things_media import (
    build_narrated_pitch_cues,
    extract_screenplay_dialogue,
    pitch_narration_text,
)


class AllThingsMediaTests(unittest.TestCase):
    def test_live_explicit_grid_narrates_each_of_nine_cards_in_order(self) -> None:
        rows = (
            (1, "Shot 1.1: Establish the cramped harbor radio workshop as a storm rattles the windows.", "HARBOR RADIO WORKSHOP, DAWN"),
            (1, "Shot 1.2: Close-up of Lila holding a handwritten frequency note beside the receiver.", "HARBOR RADIO WORKSHOP, DAWN"),
            (1, "Shot 1.3: Medium shot of Theo nodding and grabbing the tool bag.", "HARBOR RADIO WORKSHOP, DAWN"),
            (2, "Shot 2.1: Wide shot establishes the storm-battered rooftop transmitter.", "ROOFTOP TRANSMITTER, STORM"),
            (2, "Shot 2.2: Close-up of a fraying cable whipping against the antenna mast.", "ROOFTOP TRANSMITTER, STORM"),
            (2, "Shot 2.3: Full-body shot of Lila and Theo struggling together to secure the cable.", "ROOFTOP TRANSMITTER, STORM"),
            (3, "Shot 3.1: The radio receiver sparks and a green indicator turns on.", "BACK IN HARBOR RADIO WORKSHOP, MOMENTS LATER"),
            (3, "Shot 3.2: Lila broadcasts a clear warning into the microphone.", "BACK IN HARBOR RADIO WORKSHOP, MOMENTS LATER"),
            (3, "Shot 3.3: Lila and Theo exchange relieved looks as the storm begins to fade.", "BACK IN HARBOR RADIO WORKSHOP, MOMENTS LATER"),
        )
        brief = ProductionBrief.from_mapping(
            {
                "title": "Harbor Signal",
                "summary": "Lila and Theo repair a storm-damaged transmitter and warn the harbor.",
                "format": "short drama",
                "target_audience": "general audiences",
                "duration_seconds": 54,
                "genre": "drama",
                "tone": ["urgent", "hopeful"],
                "visual_direction": "Grounded coastal line art.",
                "audio_direction": "Storm, radio static, and clear dialogue.",
                "deliverables": ["storyboard pitch"],
                "scenes": [
                    {
                        "number": index,
                        "purpose": purpose,
                        "setting": setting,
                        "characters": ["Lila", "Theo"],
                        "dialogue_required": logical_scene in {1, 3},
                    }
                    for index, (logical_scene, purpose, setting) in enumerate(rows, start=1)
                ],
                "clarifying_questions": [],
                "ready_for_production": True,
            }
        )
        timeline = compile_storyboard_timeline(brief)
        cues = build_narrated_pitch_cues(brief.to_dict(), timeline, source_message="")

        self.assertEqual(len(brief.scenes), 3)
        self.assertEqual(len(cues), 9)
        self.assertEqual(
            [cue["action"] for cue in cues],
            [purpose.split(":", 1)[1].strip() for _scene, purpose, _setting in rows],
        )
        for cue in cues:
            self.assertNotRegex(cue["narration"], r"\bShot\s+\d+\.\d+\b")
            self.assertNotIn("the scene subjects", cue["narration"].casefold())
            self.assertNotIn("lila face the next turn", cue["narration"].casefold())

        self.assertIn("Lila holds a handwritten frequency note", cues[1]["narration"])
        self.assertIn("Theo nods and grabs the tool bag", cues[2]["narration"])
        self.assertIn("storm rattles the windows", cues[0]["narration"])
        self.assertIn("storm-battered rooftop transmitter", cues[3]["narration"])
        self.assertTrue(
            cues[4]["narration"].startswith(
                "A fraying cable whips against the antenna mast."
            )
        )
        self.assertIn("Lila and Theo struggle together to secure the cable", cues[5]["narration"])
        self.assertTrue(cues[0]["narration"].startswith("In HARBOR RADIO WORKSHOP, DAWN,"))
        self.assertTrue(
            cues[0]["narration"].startswith(
                "In HARBOR RADIO WORKSHOP, DAWN, we see"
            )
        )
        self.assertFalse(cues[1]["narration"].startswith(("In ", "Back in ")))
        self.assertTrue(cues[3]["narration"].startswith("In ROOFTOP TRANSMITTER, STORM,"))
        self.assertTrue(
            cues[3]["narration"].startswith(
                "In ROOFTOP TRANSMITTER, STORM, we see"
            )
        )
        self.assertTrue(
            cues[6]["narration"].startswith(
                "Back in HARBOR RADIO WORKSHOP, MOMENTS LATER, the radio receiver"
            )
        )
        self.assertEqual(sum(cue["narration"].startswith("Back in ") for cue in cues), 1)
        self.assertFalse(cues[7]["narration"].startswith(("In ", "Back in ")))
        self.assertNotIn(
            "The next story beat unfolds",
            " ".join(cue["narration"] for cue in cues),
        )
        self.assertLessEqual(
            sum(
                len(re.findall(r"\bFinally\b", cue["narration"], re.IGNORECASE))
                for cue in cues
            ),
            1,
        )

    def test_failed_live_grid_binds_dialogue_and_finite_visuals_to_exact_cards(self) -> None:
        actions = (
            "Close-up of Lila's hands carefully holding the copper tuning key in the dim dawn light.",
            "Medium shot of Lila and Theo hunched over the disassembled storm receiver. Lila says, 'The copper tuning key is our last chance.'",
            "Theo looks up resolutely, preparing to head outside. Theo answers, 'Then I will hold the mast steady.'",
            "Medium tracking shot of Lila and Theo carrying the copper tuning key upstairs onto the exposed platform.",
            "Wide shot as a violent gust of wind tears an antenna cable loose, whipping it wildly.",
            "Close-up of Lila's hands fighting the wind to reconnect the cable while Theo throws his weight against the shaking mast to brace it.",
            "Medium shot of the storm receiver's vacuum tubes glowing to life as static clears.",
            "Close-up of Lila speaking urgently into the radio microphone, broadcasting the storm warning.",
            "Medium shot of Lila and Theo exchanging a tired, deeply relieved look as the transmission succeeds.",
        )
        locations = (
            "HARBOR RADIO WORKSHOP, DAWN",
            "HARBOR RADIO WORKSHOP, DAWN",
            "HARBOR RADIO WORKSHOP, DAWN",
            "ROOFTOP TRANSMITTER, STORM",
            "ROOFTOP TRANSMITTER, STORM",
            "ROOFTOP TRANSMITTER, STORM",
            "BACK IN HARBOR RADIO WORKSHOP, MOMENTS LATER",
            "BACK IN HARBOR RADIO WORKSHOP, MOMENTS LATER",
            "BACK IN HARBOR RADIO WORKSHOP, MOMENTS LATER",
        )
        roles = ("establishing", "primary_coverage", "continuity_bridge") * 3
        timeline = {
            "shots": [
                {
                    "shot_id": f"SC{((index - 1) // 3) + 1:02d}-SH{((index - 1) % 3) + 1:02d}",
                    "story_scene_number": ((index - 1) // 3) + 1,
                    "scene_number": index,
                    "role": roles[index - 1],
                    "storyboard_card": {
                        "action": action,
                        "location": locations[index - 1],
                        "dialogue_or_audio": "",
                    },
                }
                for index, action in enumerate(actions, start=1)
            ]
        }
        source = """INT. HARBOR RADIO WORKSHOP - DAWN

LILA
The copper tuning key is our last chance.

THEO
Then I will hold the mast steady.

EXT. ROOFTOP TRANSMITTER - STORM

INT. HARBOR RADIO WORKSHOP - MOMENTS LATER
"""

        cues = build_narrated_pitch_cues(
            {"title": "Harbor Signal", "summary": "A storm threatens the harbor."},
            timeline,
            source_message=source,
        )

        self.assertEqual(
            [cue["narration"] for cue in cues],
            [
                "In HARBOR RADIO WORKSHOP, DAWN, Lila's hands carefully hold the copper tuning key in the dim dawn light.",
                'Lila says, "The copper tuning key is our last chance."',
                'Theo says, "Then I will hold the mast steady."',
                "In ROOFTOP TRANSMITTER, STORM, Lila and Theo carry the copper tuning key upstairs onto the exposed platform.",
                "A violent gust of wind tears an antenna cable loose, whipping it wildly.",
                "Lila's hands fight the wind to reconnect the cable while Theo throws his weight against the shaking mast to brace it.",
                "Back in HARBOR RADIO WORKSHOP, MOMENTS LATER, the storm receiver's vacuum tubes glow to life as static clears.",
                "Lila speaks urgently into the radio microphone, broadcasting the storm warning.",
                "Lila and Theo exchange a tired, deeply relieved look as the transmission succeeds.",
            ],
        )
        narration = " ".join(cue["narration"] for cue in cues)
        self.assertNotRegex(narration, r"(?i)\b(?:medium tracking shot|medium shot|wide shot|close-up)\b")
        self.assertEqual(narration.count("The copper tuning key is our last chance."), 1)
        self.assertEqual(narration.count("Then I will hold the mast steady."), 1)
        self.assertEqual([cue["sequence"] for cue in cues if cue["narration"].startswith(("In ", "Back in "))], [1, 4, 7])
        self.assertEqual(cues[1]["dialogue_lines"], ["LILA: The copper tuning key is our last chance."])
        self.assertEqual(cues[2]["dialogue_lines"], ["THEO: Then I will hold the mast steady."])

    def test_rejected_live_visual_variants_strip_framing_and_use_finite_verbs(self) -> None:
        actions = (
            "Close-up on the intricate copper tuning key in Lila's hands.",
            "Medium shot of Lila and Theo working over the disassembled storm receiver in the harbor radio workshop at dawn.",
            "Lila tests the tuning dial.",
            "Low-angle shot of Lila and Theo carrying the copper tuning key up the stairs to the rooftop antenna platform.",
            "Theo braces the antenna mast against the wind.",
            "Lila reconnecting the cable to the terminal while Theo uses both hands to steady the mast.",
            "Medium shot back in the harbor radio workshop as the storm receiver's vacuum tubes glow warm orange and the device hums back to life.",
        )
        locations = (
            "HARBOR RADIO WORKSHOP",
            "HARBOR RADIO WORKSHOP",
            "HARBOR RADIO WORKSHOP",
            "ROOFTOP TRANSMITTER",
            "ROOFTOP TRANSMITTER",
            "ROOFTOP TRANSMITTER",
            "BACK IN HARBOR RADIO WORKSHOP",
        )
        timeline = {
            "shots": [
                {
                    "shot_id": f"SC01-SH{index:02d}",
                    "scene_number": 1,
                    "role": "primary_coverage",
                    "storyboard_card": {
                        "action": action,
                        "location": locations[index - 1],
                        "dialogue_or_audio": "",
                    },
                }
                for index, action in enumerate(actions, start=1)
            ]
        }

        cues = build_narrated_pitch_cues({}, timeline, source_message="")

        self.assertEqual(
            cues[0]["narration"],
            "In HARBOR RADIO WORKSHOP, the intricate copper tuning key rests in Lila's hands.",
        )
        self.assertEqual(
            cues[1]["narration"],
            "Lila and Theo work over the disassembled storm receiver at dawn.",
        )
        self.assertEqual(
            cues[3]["narration"],
            "In ROOFTOP TRANSMITTER, Lila and Theo carry the copper tuning key up the stairs to the rooftop antenna platform.",
        )
        self.assertEqual(
            cues[5]["narration"],
            "Lila reconnects the cable to the terminal while Theo uses both hands to steady the mast.",
        )
        self.assertEqual(
            cues[6]["narration"],
            "Back in HARBOR RADIO WORKSHOP, the storm receiver's vacuum tubes glow warm orange and the device hums back to life.",
        )
        narration = " ".join(cue["narration"] for cue in cues)
        self.assertNotRegex(
            narration,
            r"(?i)\b(?:close-up|medium shot|wide shot|low-angle shot|high-angle shot|"
            r"overhead shot|full-body shot|two-shot|insert)\b",
        )
        self.assertEqual(cues[0]["narration"].casefold().count("harbor radio workshop"), 1)
        self.assertEqual(cues[6]["narration"].casefold().count("harbor radio workshop"), 1)

    def test_latest_live_narration_failures_are_rewritten_as_finite_story_prose(self) -> None:
        actions = (
            "Lila's hands adjusting the delicate gears of the storm receiver.",
            "Lila checks the receiver's wiring.",
            "Theo packs the repair tools.",
            "Lila and Theo climbing onto the wind-swept rooftop carry the copper tuning key.",
            "Theo braces the mast against the wind.",
            "Lila reconnects the antenna cable.",
            "The storm receiver's dial suddenly glows and coming alive with static.",
            "Lila transmits the harbor warning.",
            "Medium-wide shot of Lila and Theo exchange an exhausted, relieved look as the warning transmits successfully.",
        )
        locations = (
            "HARBOR RADIO WORKSHOP, DAWN",
            "HARBOR RADIO WORKSHOP, DAWN",
            "HARBOR RADIO WORKSHOP, DAWN",
            "ROOFTOP ANTENNA PLATFORM, CONTINUOUS",
            "ROOFTOP ANTENNA PLATFORM, CONTINUOUS",
            "ROOFTOP ANTENNA PLATFORM, CONTINUOUS",
            "BACK IN HARBOR RADIO WORKSHOP, MOMENTS LATER",
            "BACK IN HARBOR RADIO WORKSHOP, MOMENTS LATER",
            "BACK IN HARBOR RADIO WORKSHOP, MOMENTS LATER",
        )
        timeline = {
            "shots": [
                {
                    "shot_id": f"SC{((index - 1) // 3) + 1:02d}-SH{((index - 1) % 3) + 1:02d}",
                    "story_scene_number": ((index - 1) // 3) + 1,
                    "scene_number": index,
                    "role": "primary_coverage",
                    "storyboard_card": {
                        "action": action,
                        "location": locations[index - 1],
                        "dialogue_or_audio": "",
                    },
                }
                for index, action in enumerate(actions, start=1)
            ]
        }

        cues = build_narrated_pitch_cues({}, timeline, source_message="")

        self.assertEqual(
            cues[0]["narration"],
            "In HARBOR RADIO WORKSHOP, DAWN, Lila's hands adjust the delicate gears of the storm receiver.",
        )
        self.assertEqual(
            cues[3]["narration"],
            "In ROOFTOP ANTENNA PLATFORM, CONTINUOUS, Lila and Theo carry the copper tuning key while climbing onto the wind-swept rooftop.",
        )
        self.assertEqual(
            cues[6]["narration"],
            "Back in HARBOR RADIO WORKSHOP, MOMENTS LATER, the storm receiver's dial suddenly glows and comes alive with static.",
        )
        self.assertEqual(
            cues[8]["narration"],
            "Lila and Theo exchange an exhausted, relieved look as the warning transmits successfully.",
        )
        narration = " ".join(cue["narration"] for cue in cues)
        self.assertNotRegex(narration, r"(?i)\bmedium[- ]wide shot\b")
        self.assertNotRegex(narration, r"(?i)\b(?:hands adjusting|and coming alive)\b")

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
