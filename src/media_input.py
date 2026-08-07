"""Resolve local recordings and supported video links into local media files."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from urllib.parse import urlparse


YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}


def resolve_ffmpeg_location() -> str:
    configured = os.getenv("FFMPEG_PATH", "ffmpeg").strip()
    candidate = Path(configured).expanduser()
    executable = str(candidate.resolve()) if candidate.is_file() else shutil.which(configured)
    if not executable:
        raise RuntimeError("YouTube inputs require FFmpeg for video and audio merging.")
    return str(Path(executable).resolve().parent)


def is_youtube_url(value: str) -> bool:
    parsed = urlparse(str(value or "").strip())
    return parsed.scheme in {"http", "https"} and parsed.hostname in YOUTUBE_HOSTS


def download_youtube_video(url: str, output_dir: str | Path) -> Path:
    """Download one YouTube video through the optional yt-dlp adapter."""
    if not is_youtube_url(url):
        raise ValueError("Only YouTube video URLs are supported as remote media inputs.")
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise RuntimeError("YouTube inputs require the yt-dlp package.") from exc

    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    options = {
        "format": "bestvideo*+bestaudio/best",
        "merge_output_format": "mp4",
        "ffmpeg_location": resolve_ffmpeg_location(),
        "noplaylist": True,
        "restrictfilenames": True,
        "outtmpl": str(destination / "%(id)s.%(ext)s"),
        "quiet": True,
    }
    with YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=True)
        merged = destination / f"{info.get('id')}.mp4"
        if merged.exists():
            return merged.resolve()
        requested = info.get("requested_downloads") or []
        candidate = requested[0].get("filepath") if requested else None
        if not candidate:
            candidate = downloader.prepare_filename(info)
    path = Path(str(candidate)).resolve()
    if not path.exists() and path.with_suffix(".mp4").exists():
        path = path.with_suffix(".mp4")
    if not path.is_file():
        raise RuntimeError("yt-dlp completed without producing a local video file.")
    return path


def resolve_media_input(value: str | Path, output_dir: str | Path) -> Path:
    raw = str(value).strip()
    if is_youtube_url(raw):
        return download_youtube_video(raw, output_dir)
    parsed = urlparse(raw)
    if parsed.scheme in {"http", "https"}:
        raise ValueError("Remote media must be a supported YouTube URL.")
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Recording does not exist: {path}")
    return path
