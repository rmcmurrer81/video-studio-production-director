from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from kira_studio.all_things_agentic import (
    AllThingsConfig,
    VisualPanelGenerationError,
)
from kira_studio.all_things_google import GoogleGenAIVisualPanelProvider


def valid_config() -> AllThingsConfig:
    return AllThingsConfig(project="video-studio-12345")


def still_image_bytes(*, image_format: str = "PNG", metadata: bool = False) -> bytes:
    image = Image.new("RGB", (960, 540), (241, 238, 228))
    draw = ImageDraw.Draw(image)
    draw.rectangle((55, 45, 905, 495), outline=(28, 37, 54), width=8)
    draw.ellipse((245, 95, 475, 325), outline=(22, 62, 85), width=10)
    draw.line((485, 350, 780, 150), fill=(105, 44, 35), width=12)
    output = BytesIO()
    arguments: dict[str, object] = {"format": image_format}
    if metadata and image_format == "JPEG":
        exif = Image.Exif()
        exif[0x010E] = "private test description"
        arguments["exif"] = exif
        arguments["icc_profile"] = b"test-profile"
    image.save(output, **arguments)
    return output.getvalue()


def animated_image_bytes() -> bytes:
    first = Image.new("RGB", (128, 72), "white")
    second = Image.new("RGB", (128, 72), "black")
    output = BytesIO()
    first.save(
        output,
        format="GIF",
        save_all=True,
        append_images=[second],
        duration=100,
        loop=0,
    )
    return output.getvalue()


def image_response(
    data: bytes | None,
    *,
    response_id: object = "response-visual-1",
    thought_data: bytes | None = None,
) -> object:
    parts: list[object] = []
    if thought_data is not None:
        parts.append(
            SimpleNamespace(
                thought=True,
                inline_data=SimpleNamespace(
                    data=thought_data,
                    mime_type="image/png",
                ),
            )
        )
    if data is not None:
        parts.append(
            SimpleNamespace(
                thought=False,
                inline_data=SimpleNamespace(data=data, mime_type="image/png"),
            )
        )
    return SimpleNamespace(
        response_id=response_id,
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=parts))],
    )


class FakeModels:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    def generate_content(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeClient:
    def __init__(self, *outcomes: object) -> None:
        self.models = FakeModels(list(outcomes))


class StatusError(Exception):
    def __init__(self, status_code: int, private_message: str) -> None:
        super().__init__(private_message)
        self.status_code = status_code


class GoogleVisualPanelProviderTests(unittest.TestCase):
    def test_generates_static_bounded_metadata_free_jpeg_with_exact_evidence(self) -> None:
        source = still_image_bytes(image_format="JPEG", metadata=True)
        client = FakeClient(
            image_response(
                source,
                thought_data=b"not a real image and must be ignored",
            )
        )
        provider = GoogleGenAIVisualPanelProvider(valid_config(), client=client)

        result = provider.create_panel(
            "Black ink storyboard frame of two friends in an orbital repair shop.",
            shot_id="SC01-SH01",
            job_id="00000000-0000-0000-0000-000000000001",
        )

        self.assertEqual(result.mime_type, "image/jpeg")
        self.assertEqual((result.width, result.height), (768, 432))
        self.assertLessEqual(len(result.image_bytes), 45_000)
        with Image.open(BytesIO(result.image_bytes)) as rendered:
            self.assertEqual(rendered.format, "JPEG")
            self.assertEqual(rendered.mode, "RGB")
            self.assertEqual(rendered.size, (768, 432))
            self.assertEqual(len(rendered.getexif()), 0)
            self.assertNotIn("icc_profile", rendered.info)
            self.assertFalse(getattr(rendered, "is_animated", False))

        call = client.models.calls[0]
        self.assertEqual(call["model"], "gemini-3.1-flash-image")
        self.assertIsInstance(call["contents"], str)
        config = call["config"]
        self.assertEqual(config.response_modalities, ["TEXT", "IMAGE"])
        self.assertEqual(config.image_config.aspect_ratio, "16:9")
        self.assertEqual(config.image_config.image_size, "1K")
        self.assertEqual(
            set(result.execution),
            {
                "provider",
                "framework",
                "api_version",
                "model",
                "project",
                "location",
                "evidence_origin",
                "response_id",
                "shot_id",
                "job_id",
            },
        )
        self.assertEqual(result.execution["provider"], "Vertex AI")
        self.assertEqual(result.execution["framework"], "google-genai")
        self.assertEqual(result.execution["api_version"], "v1")
        self.assertEqual(result.execution["evidence_origin"], "injected_test_client")
        self.assertEqual(result.execution["response_id"], "response-visual-1")

    def test_includes_optional_reference_part_and_falls_back_if_it_cannot_be_built(self) -> None:
        source = still_image_bytes()
        client = FakeClient(image_response(source), image_response(source))
        provider = GoogleGenAIVisualPanelProvider(valid_config(), client=client)

        provider.create_panel(
            "Continue the same character identity.",
            shot_id="SC01-SH02",
            job_id="job-2",
            reference_image=source,
        )
        contents = client.models.calls[0]["contents"]
        self.assertIsInstance(contents, list)
        self.assertEqual(contents[0].inline_data.data, source)
        self.assertEqual(contents[0].inline_data.mime_type, "image/jpeg")

        from google.genai import types

        with patch.object(types.Part, "from_bytes", side_effect=ValueError("private")):
            provider.create_panel(
                "Make a fresh safe panel.",
                shot_id="SC01-SH03",
                job_id="job-2",
                reference_image=source,
            )
        self.assertEqual(client.models.calls[1]["contents"], "Make a fresh safe panel.")

    def test_rate_limit_is_deferred_without_an_immediate_provider_retry(self) -> None:
        client = FakeClient(
            StatusError(429, "private key-like text"),
            image_response(still_image_bytes()),
        )
        provider = GoogleGenAIVisualPanelProvider(valid_config(), client=client)
        with patch("kira_studio.all_things_google.time.sleep") as sleep:
            with self.assertRaises(VisualPanelGenerationError) as caught:
                provider.create_panel(
                    "Defer a quota-limited image request.",
                    shot_id="SC01-SH01",
                    job_id="job-3",
                )
        self.assertEqual(caught.exception.code, "quota_or_rate_limited")
        self.assertEqual(len(client.models.calls), 1)
        sleep.assert_not_called()

    def test_exhausted_rate_limit_and_5xx_return_only_allowlisted_codes(self) -> None:
        rate_client = FakeClient(
            StatusError(429, "secret one"),
            StatusError(429, "secret two"),
            StatusError(429, "secret three"),
            StatusError(429, "secret four"),
            StatusError(429, "secret five"),
        )
        with patch("kira_studio.all_things_google.time.sleep") as rate_sleep:
            with self.assertRaises(VisualPanelGenerationError) as caught:
                GoogleGenAIVisualPanelProvider(
                    valid_config(), client=rate_client
                ).create_panel(
                    "Rate limited panel.",
                    shot_id="SC01-SH01",
                    job_id="job-4",
                )
        self.assertEqual(caught.exception.code, "quota_or_rate_limited")
        self.assertNotIn("secret", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertEqual(len(rate_client.models.calls), 1)
        rate_sleep.assert_not_called()

        server_client = FakeClient(
            StatusError(503, "secret one"),
            StatusError(503, "secret two"),
            StatusError(503, "secret three"),
            StatusError(503, "secret four"),
            StatusError(503, "secret five"),
        )
        with patch("kira_studio.all_things_google.time.sleep") as server_sleep:
            with self.assertRaises(VisualPanelGenerationError) as caught:
                GoogleGenAIVisualPanelProvider(
                    valid_config(), client=server_client
                ).create_panel(
                    "Unavailable panel.",
                    shot_id="SC01-SH01",
                    job_id="job-5",
                )
        self.assertEqual(caught.exception.code, "generation_failed")
        self.assertNotIn("secret", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        # One global-gate reservation corresponds to exactly one provider
        # request.  A 5xx is surfaced to the durable orchestrator instead of
        # being retried invisibly inside the reserved request slot.
        self.assertEqual(len(server_client.models.calls), 1)
        server_sleep.assert_not_called()

    def test_nonretryable_provider_error_is_not_retried(self) -> None:
        client = FakeClient(StatusError(400, "private rejected prompt detail"))
        with patch("kira_studio.all_things_google.time.sleep") as sleep:
            with self.assertRaises(VisualPanelGenerationError) as caught:
                GoogleGenAIVisualPanelProvider(valid_config(), client=client).create_panel(
                    "Rejected panel.",
                    shot_id="SC01-SH01",
                    job_id="job-6",
                )
        self.assertEqual(caught.exception.code, "generation_failed")
        self.assertEqual(len(client.models.calls), 1)
        self.assertNotIn("private", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        sleep.assert_not_called()

    def test_success_does_not_sleep_or_retry(self) -> None:
        client = FakeClient(image_response(still_image_bytes()))
        with patch("kira_studio.all_things_google.time.sleep") as sleep:
            GoogleGenAIVisualPanelProvider(valid_config(), client=client).create_panel(
                "Successful panel.",
                shot_id="SC01-SH01",
                job_id="job-6",
            )
        self.assertEqual(len(client.models.calls), 1)
        sleep.assert_not_called()

    def test_blocked_missing_or_unsafe_provider_assets_are_rejected(self) -> None:
        cases = (
            (image_response(None), "provider_blocked"),
            (image_response(b"not an image"), "invalid_provider_asset"),
            (image_response(animated_image_bytes()), "invalid_provider_asset"),
            (
                image_response(b"x" * (12 * 1024 * 1024 + 1)),
                "invalid_provider_asset",
            ),
        )
        for response, expected_code in cases:
            with self.subTest(expected_code=expected_code, response=response):
                with self.assertRaises(VisualPanelGenerationError) as caught:
                    GoogleGenAIVisualPanelProvider(
                        valid_config(), client=FakeClient(response)
                    ).create_panel(
                        "One safe storyboard panel.",
                        shot_id="SC01-SH01",
                        job_id="job-7",
                    )
                self.assertEqual(caught.exception.code, expected_code)

    def test_untrusted_identifiers_and_response_id_are_bounded(self) -> None:
        provider = GoogleGenAIVisualPanelProvider(
            valid_config(), client=FakeClient(image_response(still_image_bytes()))
        )
        with self.assertRaises(VisualPanelGenerationError) as caught:
            provider.create_panel(
                "Panel.",
                shot_id="../../secret",
                job_id="job-8",
            )
        self.assertEqual(caught.exception.code, "generation_failed")

        client = FakeClient(
            image_response(still_image_bytes(), response_id="secret value with spaces")
        )
        result = GoogleGenAIVisualPanelProvider(
            valid_config(), client=client
        ).create_panel(
            "Panel with unsafe response ID.",
            shot_id="SC01-SH01",
            job_id="job-8",
        )
        self.assertIsNone(result.execution["response_id"])


if __name__ == "__main__":
    unittest.main()
