from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "web" / "all-things-agentic.html"


class _PageContract(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.labels: dict[str, str] = {}
        self.attributes: dict[str, dict[str, str | None]] = {}
        self.external_sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        element_id = values.get("id")
        if element_id:
            self.ids.append(element_id)
            self.attributes[element_id] = values
        if tag == "label" and values.get("for"):
            self.labels[str(values["for"])] = values.get("for") or ""
        for name in ("src", "href"):
            value = values.get(name)
            if value and re.match(r"^(?:https?:)?//", value):
                self.external_sources.append(value)


class AllThingsAgenticUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = PAGE.read_text(encoding="utf-8")
        cls.parser = _PageContract()
        cls.parser.feed(cls.html)

    def test_legacy_access_and_job_controls_remain_unique_and_labeled(self) -> None:
        required = {
            "access",
            "message",
            "submit",
            "cancel",
            "retry",
            "error",
            "state",
            "stage",
            "progress",
            "bar",
            "eta",
            "job",
            "brief",
        }
        self.assertTrue(required.issubset(self.parser.ids))
        for element_id in required:
            self.assertEqual(self.parser.ids.count(element_id), 1, element_id)
        self.assertIn("access", self.parser.labels)
        self.assertIn("message", self.parser.labels)
        access = self.parser.attributes["access"]
        self.assertEqual(access["type"], "password")
        self.assertEqual(access["autocomplete"], "off")
        self.assertEqual(access["maxlength"], "256")

    def test_storyboard_timeline_is_truthful_plan_only_and_self_contained(self) -> None:
        for marker in (
            'id="conversationFeed"',
            'id="timelineDock"',
            'id="timelineViewport"',
            'id="timelineTrack"',
            'id="timelinePlayhead"',
            'id="timelineSelection"',
            "Storyboard · planned edit decision list",
            "Planned non-drop 24 fps",
            "No footage has been selected, changed, or rendered.",
            "no source clip has been selected or mutated",
            "@media(max-width:980px)",
            "overflow-x:auto",
        ):
            self.assertIn(marker, self.html)
        self.assertEqual(self.parser.external_sources, [])
        self.assertNotIn("duration_seconds /", self.html)
        self.assertIn("clip.dataset.sceneNumber = String(shot.scene_number)", self.html)
        self.assertIn("clip.dataset.shotId = String(shot.shot_id)", self.html)
        self.assertIn("shot.planned_in_timecode", self.html)
        self.assertIn("shot.planned_out_timecode", self.html)
        self.assertIn("card.framing", self.html)
        self.assertIn("card.camera", self.html)
        self.assertIn("card.action", self.html)

    def test_package_json_download_is_local_sanitized_and_revoked(self) -> None:
        for marker in (
            'id="downloadPackage"',
            "function downloadStoryboardPackage()",
            "JSON.stringify(currentStoryboardPackage, null, 2)",
            "{type:'application/json'}",
            "URL.createObjectURL(blob)",
            "link.download = packageFilename()",
            "URL.revokeObjectURL(url)",
            "-storyboard-edl.json",
            ".replace(/[^a-z0-9]+/gi, '-')",
            "renderStoryboardPackage(null)",
        ):
            self.assertIn(marker, self.html)
        download_body = self.html.split("function downloadStoryboardPackage()", 1)[1].split(
            "function responseError", 1
        )[0]
        self.assertNotIn("access", download_body.casefold())

    def test_package_html_download_is_escaped_allowlisted_and_plan_only(self) -> None:
        for marker in (
            'id="downloadStoryboardSheet"',
            "Download storyboard HTML",
            "function storyboardSheetHtml()",
            "function downloadStoryboardSheet()",
            "{type:'text/html;charset=utf-8'}",
            "link.download = storyboardSheetFilename()",
            "-storyboard-sheet.html",
            "Content-Security-Policy",
            "default-src 'none'",
            "PLAN ONLY · NO RENDERED MEDIA",
            "Creative direction",
            "Planned storyboard cards",
            "Deterministic audit",
            "Source-footage guidance",
            "Bridge guidance:",
            "esc(brief.title",
            "esc(brief.summary",
            "esc(brief.visual_direction",
            "esc(brief.audio_direction",
            "esc(shot?.planned_in_timecode",
            "esc(shot?.planned_out_timecode",
            "esc(card.framing",
            "esc(card.camera",
            "esc(card.action",
            "esc(card.dialogue_or_audio",
            "esc(card.source_footage_guidance",
            "esc(card.bridge_shot_guidance",
            "items.map(value => `<li>${esc(value)}</li>`)",
            "esc(check?.evidence",
            "allowlisted fields",
        ):
            self.assertIn(marker, self.html)
        sheet_body = self.html.split("function storyboardSheetList", 1)[1].split(
            "function responseError", 1
        )[0]
        self.assertNotIn("JSON.stringify", sheet_body)
        self.assertNotIn("fetch(", sheet_body)
        self.assertNotIn("access", sheet_body.casefold())
        self.assertNotIn("secret", sheet_body.casefold())
        self.assertNotIn("<script", sheet_body.casefold())
        self.assertIn("URL.revokeObjectURL(url)", sheet_body)

    def test_same_origin_job_and_access_contract_is_unchanged(self) -> None:
        for marker in (
            "'X-Video-Studio-Access':$('access').value",
            "request('/v1/jobs', {method:'POST', body:JSON.stringify({message})})",
            "request(`/v1/jobs/${currentJob.job_id}`)",
            "request(`/v1/jobs/${currentJob.job_id}:cancel`, {method:'POST', body:'{}'})",
            "request(`/v1/jobs/${currentJob.job_id}:retry`, {method:'POST', body:'{}'})",
            "const terminal = new Set(['succeeded', 'failed', 'cancelled'])",
            "setTimeout(poll, 1200)",
        ):
            self.assertIn(marker, self.html)
        self.assertNotIn("localStorage", self.html)
        self.assertNotIn("sessionStorage", self.html)

    def test_clarification_followups_are_composed_in_memory_without_extra_api_fields(self) -> None:
        for marker in (
            'id="conversationContext"',
            "let clarificationSession = null",
            "function composeClarificationSubmission(shortAnswer)",
            "Original user request:",
            "Earlier answered clarification rounds:",
            "Current clarification questions:",
            "User\\'s current short answer:",
            "answeredRounds: [",
            "...clarificationSession.answeredRounds",
            "if (brief.ready_for_production === true)",
            "clarificationSession = null",
            "addConversationMessage('user', visibleMessage)",
            "JSON.stringify({message})",
            "no server-side chat memory",
        ):
            self.assertIn(marker, self.html)
        self.assertNotIn("JSON.stringify({message,", self.html)
        self.assertNotIn("conversation_id", self.html)
        self.assertNotIn("chat_history", self.html)

    def test_job_shaped_non_2xx_response_remains_renderable_for_retry(self) -> None:
        for marker in (
            "function responseError(payload, status)",
            "if (payload && payload.job_id) error.job = payload",
            "if (error.job) render(error.job)",
            "`${detail.code || 'Job failed'} (${detail.type || `HTTP ${status}`})`",
        ):
            self.assertIn(marker, self.html)
        self.assertNotIn("new Error(payload.error", self.html)

    def test_progress_and_status_are_accessible_without_invented_eta(self) -> None:
        progress = self.parser.attributes["progressBar"]
        self.assertEqual(progress["role"], "progressbar")
        self.assertEqual(progress["aria-valuemin"], "0")
        self.assertEqual(progress["aria-valuemax"], "100")
        self.assertIn("$('progressBar').setAttribute('aria-valuenow', String(progress))", self.html)
        self.assertIn("ETA unavailable until a real completed-job timing sample exists.", self.html)
        self.assertIn("eta.sample_count", self.html)


if __name__ == "__main__":
    unittest.main()
