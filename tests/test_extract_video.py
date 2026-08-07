import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class MediaBinaryResolutionTests(unittest.TestCase):
    @patch("src.extract_video.shutil.which", return_value=r"C:\tools\ffmpeg.exe")
    def test_resolves_command_from_path(self, which):
        from src.extract_video import resolve_media_binary

        self.assertEqual(r"C:\tools\ffmpeg.exe", resolve_media_binary("ffmpeg"))
        which.assert_called_once_with("ffmpeg")

    def test_preserves_unresolved_command_for_clear_subprocess_error(self):
        from src.extract_video import resolve_media_binary

        with patch("src.extract_video.shutil.which", return_value=None):
            self.assertEqual("missing-media-tool", resolve_media_binary("missing-media-tool"))

    def test_accepts_explicit_executable_path(self):
        from src.extract_video import resolve_media_binary

        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "ffmpeg.exe"
            executable.write_bytes(b"test")
            self.assertEqual(str(executable.resolve()), resolve_media_binary(str(executable)))


class ActionablePointTests(unittest.TestCase):
    def response(self, content):
        return SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kwargs: SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
                    )
                )
            )
        )

    @patch("src.extract_video.require_groq_client")
    def test_rejects_general_technical_advice_as_a_code_task(self, client):
        from src.extract_video import extract_key_points

        client.return_value = self.response(
            '[{"point":"Chunking matters","original_quote":"Use good chunks",'
            '"english_quote":"Use good chunks","actionable_change":false,"timestamp":0}]'
        )

        self.assertEqual([], extract_key_points("This tutorial explains why chunking matters."))

    @patch("src.extract_video.require_groq_client")
    def test_keeps_explicit_change_requests(self, client):
        from src.extract_video import extract_key_points

        client.return_value = self.response(
            '[{"point":"Add source links","original_quote":"Please add source links",'
            '"english_quote":"Please add source links","actionable_change":true,"timestamp":0}]'
        )

        points = extract_key_points("Please add source links to every answer.")
        self.assertEqual("Add source links", points[0]["point"])


class VisionAnalysisTests(unittest.TestCase):
    @patch("src.extract_video.time.sleep", return_value=None)
    @patch("src.extract_video.require_groq_client")
    def test_falls_back_when_provider_rejects_forced_json_mode(self, client, _sleep):
        from src.extract_video import analyze_frames_with_groq

        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            if "response_format" in kwargs:
                raise RuntimeError("json_validate_failed")
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"summary":"Dashboard is visible","observations":[]}'
                        )
                    )
                ]
            )

        client.return_value = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        with tempfile.TemporaryDirectory() as directory:
            frame = Path(directory) / "frame.jpg"
            from PIL import Image

            Image.new("RGB", (24, 24), "white").save(frame)
            result = analyze_frames_with_groq(
                {"point": "Update the dashboard", "english_quote": "Make the status visible"},
                [{"absolute_path": str(frame), "path": "frame.jpg"}],
            )

        self.assertEqual("Dashboard is visible", result["summary"])
        self.assertIn("response_format", calls[0])
        self.assertNotIn("response_format", calls[1])


if __name__ == "__main__":
    unittest.main()
