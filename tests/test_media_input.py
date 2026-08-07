import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.media_input import download_youtube_video, is_youtube_url, resolve_media_input


class MediaInputTests(unittest.TestCase):
    def test_detects_supported_youtube_urls(self):
        self.assertTrue(is_youtube_url("https://www.youtube.com/watch?v=abc"))
        self.assertTrue(is_youtube_url("https://youtu.be/abc"))
        self.assertFalse(is_youtube_url("https://example.com/video.mp4"))

    def test_resolves_existing_local_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "meeting.mp4"
            video.write_bytes(b"video")
            self.assertEqual(video.resolve(), resolve_media_input(video, Path(directory) / "downloads"))

    def test_rejects_unknown_remote_media(self):
        with self.assertRaisesRegex(ValueError, "YouTube"):
            resolve_media_input("https://example.com/video.mp4", "downloads")

    @patch("src.media_input.resolve_ffmpeg_location", return_value=r"C:\tools\ffmpeg\bin")
    @patch("yt_dlp.YoutubeDL")
    def test_youtube_download_passes_resolved_ffmpeg_location(self, youtube_dl, resolve_ffmpeg):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            downloader = Mock()
            youtube_dl.return_value.__enter__.return_value = downloader

            def extract_info(url, download):
                (destination / "video-id.mp4").write_bytes(b"video")
                return {"id": "video-id"}

            downloader.extract_info.side_effect = extract_info
            result = download_youtube_video("https://youtu.be/video-id", destination)

        self.assertEqual("video-id.mp4", result.name)
        self.assertEqual(r"C:\tools\ffmpeg\bin", youtube_dl.call_args.args[0]["ffmpeg_location"])
        resolve_ffmpeg.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
