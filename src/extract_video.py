"""
VisionPR - Phase 1: Multimodal Intelligence Extraction

Pipeline:
    1. Validate input video
    2. Extract audio using FFmpeg
    3. Transcribe audio using Groq Whisper
    4. Translate transcript to English if required
    5. Extract key discussion points using Groq LLM
    6. Extract contextual screenshots around key-point timestamps
    7. Analyze screenshots using Groq Vision
    8. Save final video_intelligence.json

Usage:
    python src/extract_video.py --video data/input_videos/meeting.mp4

Environment variables (.env):
    GROQ_API_KEY=your_groq_api_key

Optional:
    GROQ_WHISPER_MODEL=whisper-large-v3-turbo
    GROQ_TEXT_MODEL=llama-3.3-70b-versatile
    GROQ_VISION_MODEL=meta-llama/llama-4-scout-17b-16e-instruct
    FFMPEG_PATH=ffmpeg
    FFPROBE_PATH=ffprobe
    CONTEXT_BEFORE=3
    CONTEXT_AFTER=3
    FRAME_INTERVAL=1
    MAX_KEY_POINTS=10
"""

import argparse
import base64
import json
import logging
import mimetypes
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from groq import Groq


# ============================================================
# PATH CONFIGURATION
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = PROJECT_ROOT / "data"

INPUT_VIDEO_DIR = DATA_DIR / "input_videos"
FRAMES_DIR = DATA_DIR / "extracted_frames"
OUTPUT_JSON_DIR = DATA_DIR / "output_json"

INPUT_VIDEO_DIR.mkdir(parents=True, exist_ok=True)
FRAMES_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_JSON_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# ENVIRONMENT CONFIGURATION
# ============================================================

load_dotenv(PROJECT_ROOT / ".env")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

WHISPER_MODEL = os.getenv(
    "GROQ_WHISPER_MODEL",
    "whisper-large-v3-turbo",
)

TEXT_MODEL = os.getenv(
    "GROQ_TEXT_MODEL",
    "llama-3.3-70b-versatile",
)

VISION_MODEL = os.getenv(
    "GROQ_VISION_MODEL",
    "meta-llama/llama-4-scout-17b-16e-instruct",
)

FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")
FFPROBE_PATH = os.getenv("FFPROBE_PATH", "ffprobe")

CONTEXT_BEFORE = float(
    os.getenv("CONTEXT_BEFORE", "3")
)

CONTEXT_AFTER = float(
    os.getenv("CONTEXT_AFTER", "3")
)

FRAME_INTERVAL = float(
    os.getenv("FRAME_INTERVAL", "1")
)

MAX_KEY_POINTS = int(
    os.getenv("MAX_KEY_POINTS", "10")
)

MAX_RETRIES = 3


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("VisionPR")


# ============================================================
# GROQ CLIENT
# ============================================================

if not GROQ_API_KEY:
    raise RuntimeError(
        "GROQ_API_KEY is missing. "
        "Add GROQ_API_KEY=your_key to the .env file."
    )

client = Groq(api_key=GROQ_API_KEY)


# ============================================================
# GENERAL HELPERS
# ============================================================

def retry_operation(func, *args, **kwargs):
    """
    Retry API or external operations with exponential backoff.
    """

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):

        try:
            return func(*args, **kwargs)

        except Exception as exc:

            last_error = exc

            if attempt == MAX_RETRIES:
                break

            wait_time = 2 ** attempt

            logger.warning(
                "Operation failed (attempt %d/%d): %s",
                attempt,
                MAX_RETRIES,
                exc,
            )

            logger.info(
                "Retrying in %d seconds...",
                wait_time,
            )

            time.sleep(wait_time)

    raise RuntimeError(
        f"Operation failed after {MAX_RETRIES} attempts: "
        f"{last_error}"
    )


def parse_json_response(text: str) -> Any:
    """
    Robustly parse JSON returned by an LLM.

    Handles:
        - Plain JSON
        - Markdown code fences
        - Extra text surrounding JSON
    """

    if not text:
        raise ValueError("Empty LLM response.")

    cleaned = text.strip()

    # Remove markdown code fences
    cleaned = re.sub(
        r"^```(?:json)?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    cleaned = re.sub(
        r"\s*```$",
        "",
        cleaned,
    )

    # Direct parsing
    try:
        return json.loads(cleaned)

    except json.JSONDecodeError:
        pass

    # Try extracting JSON object
    object_match = re.search(
        r"\{.*\}",
        cleaned,
        flags=re.DOTALL,
    )

    if object_match:

        try:
            return json.loads(
                object_match.group(0)
            )

        except json.JSONDecodeError:
            pass

    # Try extracting JSON array
    array_match = re.search(
        r"\[.*\]",
        cleaned,
        flags=re.DOTALL,
    )

    if array_match:

        try:
            return json.loads(
                array_match.group(0)
            )

        except json.JSONDecodeError:
            pass

    raise ValueError(
        f"Could not parse JSON from LLM response:\n{cleaned}"
    )


def run_command(
    command: list[str],
    description: str,
) -> None:

    logger.info(
        "Running: %s",
        description,
    )

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:

        raise RuntimeError(
            f"{description} failed.\n"
            f"Command: {' '.join(command)}\n"
            f"Error: {result.stderr}"
        )


# ============================================================
# VIDEO VALIDATION
# ============================================================

def validate_video(
    video_path: Path,
) -> None:

    if not video_path.exists():

        raise FileNotFoundError(
            f"Video file does not exist: {video_path}"
        )

    if video_path.stat().st_size == 0:

        raise ValueError(
            f"Video file is empty: {video_path}"
        )

    allowed_extensions = {
        ".mp4",
        ".mov",
        ".mkv",
        ".avi",
        ".webm",
        ".m4v",
    }

    if video_path.suffix.lower() not in allowed_extensions:

        raise ValueError(
            f"Unsupported video format: "
            f"{video_path.suffix}"
        )


# ============================================================
# VIDEO METADATA
# ============================================================

def get_video_metadata(
    video_path: Path,
) -> dict[str, Any]:

    command = [
        FFPROBE_PATH,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(video_path),
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:

        logger.warning(
            "Could not retrieve video duration."
        )

        return {
            "duration_seconds": None,
        }

    try:

        data = json.loads(
            result.stdout
        )

        duration = float(
            data["format"]["duration"]
        )

        return {
            "duration_seconds": round(
                duration,
                2,
            ),
        }

    except Exception:

        return {
            "duration_seconds": None,
        }


# ============================================================
# STEP 1 - EXTRACT AUDIO
# ============================================================

def extract_audio(
    video_path: Path,
    output_dir: Path,
) -> Path:

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    audio_path = (
        output_dir /
        f"{video_path.stem}.wav"
    )

    command = [
        FFMPEG_PATH,
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(audio_path),
    ]

    run_command(
        command,
        "Extracting audio with FFmpeg",
    )

    if not audio_path.exists():

        raise RuntimeError(
            "FFmpeg completed but audio file "
            "was not created."
        )

    logger.info(
        "Audio extracted: %s",
        audio_path,
    )

    return audio_path


# ============================================================
# STEP 2 - GROQ WHISPER TRANSCRIPTION
# ============================================================

def transcribe_audio(
    audio_path: Path,
) -> tuple[
    list[dict[str, Any]],
    str,
    str,
]:

    logger.info(
        "Transcribing audio using Groq Whisper..."
    )

    def call_whisper():

        with open(
            audio_path,
            "rb",
        ) as audio_file:

            return client.audio.transcriptions.create(
                file=audio_file,
                model=WHISPER_MODEL,
                response_format="verbose_json",
                timestamp_granularities=[
                    "segment"
                ],
            )

    response = retry_operation(
        call_whisper
    )

    transcript_segments = []

    full_text_parts = []

    for segment in response.segments:

        if isinstance(
            segment,
            dict,
        ):

            text = str(
                segment.get(
                    "text",
                    "",
                )
            ).strip()

            start = float(
                segment.get(
                    "start",
                    0,
                )
            )

            end = float(
                segment.get(
                    "end",
                    0,
                )
            )

        else:

            text = str(
                getattr(
                    segment,
                    "text",
                    "",
                )
            ).strip()

            start = float(
                getattr(
                    segment,
                    "start",
                    0,
                )
            )

            end = float(
                getattr(
                    segment,
                    "end",
                    0,
                )
            )

        if not text:
            continue

        transcript_segments.append(
            {
                "start": round(
                    start,
                    2,
                ),
                "end": round(
                    end,
                    2,
                ),
                "text": text,
            }
        )

        full_text_parts.append(
            text
        )

    full_text = " ".join(
        full_text_parts
    )

    detected_language = str(
        getattr(
            response,
            "language",
            "unknown",
        )
    )

    logger.info(
        "Transcription complete. "
        "Segments: %d | Language: %s",
        len(transcript_segments),
        detected_language,
    )

    return (
        transcript_segments,
        full_text,
        detected_language,
    )


# ============================================================
# STEP 3 - TRANSLATE TO ENGLISH
# ============================================================

def translate_to_english(
    transcript: str,
    detected_language: str,
) -> str:

    if (
        detected_language.lower()
        in {
            "en",
            "eng",
            "english",
        }
    ):

        return transcript

    logger.info(
        "Translating transcript from %s to English...",
        detected_language,
    )

    prompt = f"""
Translate the following meeting transcript into English.

Rules:
- Preserve the original meaning exactly.
- Do not summarize.
- Do not omit information.
- Preserve technical terminology.
- Preserve names, numbers, filenames, error messages,
  programming terms, and product names.
- Return ONLY the translated transcript.
- Do not add explanations.

Detected language:
{detected_language}

Transcript:
{transcript}
"""

    def call_translation():

        response = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a highly accurate "
                        "technical translator."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0,
        )

        return response.choices[0].message.content

    translated = retry_operation(
        call_translation
    )

    if not translated:

        raise RuntimeError(
            "Translation returned an empty response."
        )

    return translated.strip()


# ============================================================
# STEP 4 - EXTRACT KEY POINTS
# ============================================================

def extract_key_points(
    transcript: str,
) -> list[dict[str, Any]]:

    logger.info(
        "Extracting key discussion points..."
    )

    prompt = f"""
Analyze the following meeting transcript.

Identify the most important actionable points
that are relevant to software development.

Prioritize:
1. Bug reports
2. Errors
3. Feature requests
4. UI/UX issues
5. Performance problems
6. Requested code changes
7. Technical requirements

Ignore:
- Greetings
- Small talk
- Repeated statements
- Irrelevant conversation

Return ONLY a valid JSON array.

Each item MUST have:

{{
    "point": "Short description of the issue or request",
    "original_quote": "Exact or near-exact relevant quote",
    "english_quote": "English version of the quote",
    "timestamp": 0
}}

The timestamp will be calculated separately.

Maximum number of points:
{MAX_KEY_POINTS}

Transcript:
{transcript}
"""

    def call_key_points():

        response = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a software engineering "
                        "meeting analysis agent. "
                        "Return valid JSON only."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0,
        )

        return response.choices[0].message.content

    response_text = retry_operation(
        call_key_points
    )

    parsed = parse_json_response(
        response_text
    )

    if isinstance(
        parsed,
        dict,
    ):

        parsed = (
            parsed.get("key_points")
            or parsed.get("points")
            or parsed.get("items")
            or []
        )

    if not isinstance(
        parsed,
        list,
    ):

        raise ValueError(
            "Key point extraction did not "
            "return a JSON array."
        )

    return [
        item
        for item in parsed[:MAX_KEY_POINTS]
        if isinstance(
            item,
            dict,
        )
    ]


# ============================================================
# STEP 5 - MAP KEY POINTS TO TRANSCRIPT TIMESTAMPS
# ============================================================

def calculate_key_point_timestamps(
    key_points: list[dict[str, Any]],
    transcript_segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:

    """
    Find the approximate timestamp of each key point
    by matching its quote against transcript segments.

    Uses simple token overlap rather than an external
    fuzzy-matching dependency.
    """

    def normalize_text(
        text: str,
    ) -> set[str]:

        words = re.findall(
            r"\b\w+\b",
            text.lower(),
        )

        return set(words)

    for point in key_points:

        quote = (
            point.get(
                "original_quote"
            )
            or point.get(
                "english_quote"
            )
            or ""
        )

        quote_words = normalize_text(
            str(quote)
        )

        best_score = 0
        best_segment = None

        for segment in transcript_segments:

            segment_words = normalize_text(
                segment["text"]
            )

            if not quote_words:
                continue

            intersection = (
                quote_words
                & segment_words
            )

            score = (
                len(intersection)
                / len(quote_words)
            )

            if score > best_score:

                best_score = score
                best_segment = segment

        if best_segment:

            point["timestamp"] = round(
                (
                    best_segment["start"]
                    + best_segment["end"]
                )
                / 2,
                2,
            )

        else:

            point["timestamp"] = 0.0

    return key_points


# ============================================================
# STEP 6 - EXTRACT CONTEXTUAL FRAMES
# ============================================================

def extract_context_frames(
    video_path: Path,
    timestamp: float,
    point_index: int,
    output_dir: Path,
) -> list[dict[str, Any]]:

    """
    Extract frames around a key-point timestamp.

    Example with 3 seconds before/after:

        timestamp - 3
        timestamp - 2
        timestamp - 1
        timestamp
        timestamp + 1
        timestamp + 2
        timestamp + 3

    Frames are extracted directly using FFmpeg.
    """

    point_dir = (
        output_dir /
        f"point_{point_index}"
    )

    point_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    start_time = max(
        0,
        timestamp - CONTEXT_BEFORE,
    )

    end_time = (
        timestamp
        + CONTEXT_AFTER
    )

    duration = (
        end_time
        - start_time
    )

    output_pattern = (
        point_dir /
        "frame_%03d.jpg"
    )

    # Extract frames at the configured interval.
    command = [
        FFMPEG_PATH,
        "-y",
        "-ss",
        f"{start_time:.2f}",
        "-i",
        str(video_path),
        "-t",
        f"{duration:.2f}",
        "-vf",
        f"fps=1/{FRAME_INTERVAL}",
        "-q:v",
        "2",
        str(output_pattern),
    ]

    run_command(
        command,
        f"Extracting visual context for key point {point_index}",
    )

    frames = []

    for frame_path in sorted(
        point_dir.glob(
            "frame_*.jpg"
        )
    ):

        frames.append(
            {
                "path": str(
                    frame_path.relative_to(
                        PROJECT_ROOT
                    )
                ),
                "absolute_path": str(
                    frame_path
                ),
            }
        )

    return frames


# ============================================================
# STEP 7 - GROQ VISION ANALYSIS
# ============================================================

def analyze_frames_with_groq(
    key_point: dict[str, Any],
    frames: list[dict[str, Any]],
) -> dict[str, Any]:

    if not frames:

        return {
            "summary": (
                "No visual frames were "
                "available for this key point."
            ),
            "observations": [],
        }

    content = []

    prompt = f"""
You are analyzing visual context from a software
development meeting.

The relevant discussion point is:

{key_point.get("point", "")}

Relevant quote:

{key_point.get("english_quote", "")}

Analyze the provided screenshots.

Focus on:
- Visible application UI
- Websites
- Software interfaces
- Source code
- Error messages
- Logs
- Terminal output
- Design elements
- Buttons and forms
- User interactions
- Anything visually relevant to the issue

Do NOT invent information that is not visible.

Return ONLY valid JSON with this structure:

{{
    "summary": "Concise description of what is visible",
    "observations": [
        "Observation 1",
        "Observation 2"
    ],
    "visible_errors": [
        "Error message if visible"
    ],
    "visible_files": [
        "Filename if visible"
    ],
    "relevant_ui_elements": [
        "Relevant UI element"
    ]
}}
"""

    content.append(
        {
            "type": "text",
            "text": prompt,
        }
    )

    for frame in frames:

        frame_path = Path(
            frame["absolute_path"]
        )

        if not frame_path.exists():

            continue

        mime_type = (
            mimetypes.guess_type(
                str(frame_path)
            )[0]
            or "image/jpeg"
        )

        encoded_image = base64.b64encode(
            frame_path.read_bytes()
        ).decode(
            "utf-8"
        )

        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": (
                        f"data:{mime_type};"
                        f"base64,{encoded_image}"
                    )
                },
            }
        )

    def call_vision():

        response = client.chat.completions.create(
            model=VISION_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": content,
                }
            ],
            temperature=0,
        )

        return response.choices[0].message.content

    response_text = retry_operation(
        call_vision
    )

    try:

        result = parse_json_response(
            response_text
        )

        if isinstance(
            result,
            dict,
        ):

            return result

    except Exception as exc:

        logger.warning(
            "Vision JSON parsing failed: %s",
            exc,
        )

    # Graceful fallback
    return {
        "summary": response_text,
        "observations": [],
        "visible_errors": [],
        "visible_files": [],
        "relevant_ui_elements": [],
    }


# ============================================================
# STEP 8 - BUILD FINAL JSON
# ============================================================

def build_video_intelligence(
    video_path: Path,
    metadata: dict[str, Any],
    transcript_segments: list[dict[str, Any]],
    full_transcript: str,
    english_transcript: str,
    detected_language: str,
    key_points: list[dict[str, Any]],
    visual_context: list[dict[str, Any]],
) -> dict[str, Any]:

    return {
        "project": "VisionPR",
        "phase": "Phase 1 - Multimodal Intelligence Extraction",
        "source_video": str(
            video_path.relative_to(
                PROJECT_ROOT
            )
        ),
        "video_metadata": metadata,
        "detected_language": detected_language,
        "transcript": {
            "original": full_transcript,
            "english": english_transcript,
            "segments": transcript_segments,
        },
        "key_points": key_points,
        "visual_context": visual_context,
    }


# ============================================================
# MAIN PIPELINE
# ============================================================

def process_video(
    video_path: Path,
) -> Path:

    logger.info(
        "=" * 60
    )

    logger.info(
        "VISIONPR PHASE 1 STARTED"
    )

    logger.info(
        "Input video: %s",
        video_path,
    )

    logger.info(
        "=" * 60
    )

    # --------------------------------------------------------
    # Validate
    # --------------------------------------------------------

    validate_video(
        video_path
    )

    # --------------------------------------------------------
    # Metadata
    # --------------------------------------------------------

    metadata = get_video_metadata(
        video_path
    )

    logger.info(
        "Video duration: %s seconds",
        metadata.get(
            "duration_seconds"
        ),
    )

    # --------------------------------------------------------
    # Create working directory
    # --------------------------------------------------------

    video_frames_dir = (
        FRAMES_DIR /
        video_path.stem
    )

    video_frames_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    audio_dir = (
        video_frames_dir /
        "audio"
    )

    # --------------------------------------------------------
    # Step 1 - Audio
    # --------------------------------------------------------

    audio_path = extract_audio(
        video_path,
        audio_dir,
    )

    # --------------------------------------------------------
    # Step 2 - Transcription
    # --------------------------------------------------------

    (
        transcript_segments,
        full_transcript,
        detected_language,
    ) = transcribe_audio(
        audio_path
    )

    if not transcript_segments:

        raise RuntimeError(
            "No transcript segments were generated."
        )

    # --------------------------------------------------------
    # Step 3 - Translation
    # --------------------------------------------------------

    english_transcript = (
        translate_to_english(
            full_transcript,
            detected_language,
        )
    )

    # --------------------------------------------------------
    # Step 4 - Key Points
    # --------------------------------------------------------

    key_points = extract_key_points(
        english_transcript
    )

    # --------------------------------------------------------
    # Step 5 - Timestamp Mapping
    # --------------------------------------------------------

    key_points = (
        calculate_key_point_timestamps(
            key_points,
            transcript_segments,
        )
    )

    logger.info(
        "Key points identified: %d",
        len(key_points),
    )

    # --------------------------------------------------------
    # Step 6 + 7 - Frames + Vision
    # --------------------------------------------------------

    visual_context = []

    for index, key_point in enumerate(
        key_points,
        start=1,
    ):

        timestamp = float(
            key_point.get(
                "timestamp",
                0,
            )
        )

        logger.info(
            "Processing key point %d at %.2f seconds",
            index,
            timestamp,
        )

        try:

            frames = extract_context_frames(
                video_path,
                timestamp,
                index,
                video_frames_dir,
            )

            vision_analysis = (
                analyze_frames_with_groq(
                    key_point,
                    frames,
                )
            )

            visual_context.append(
                {
                    "key_point_index": index,
                    "timestamp": timestamp,
                    "frames": [
                        frame["path"]
                        for frame in frames
                    ],
                    "analysis": vision_analysis,
                }
            )

        except Exception as exc:

            logger.error(
                "Failed visual processing "
                "for key point %d: %s",
                index,
                exc,
            )

            visual_context.append(
                {
                    "key_point_index": index,
                    "timestamp": timestamp,
                    "frames": [],
                    "analysis": {
                        "summary": (
                            "Visual analysis failed."
                        ),
                        "error": str(
                            exc
                        ),
                    },
                }
            )

    # --------------------------------------------------------
    # Step 8 - Final JSON
    # --------------------------------------------------------

    final_data = build_video_intelligence(
        video_path=video_path,
        metadata=metadata,
        transcript_segments=transcript_segments,
        full_transcript=full_transcript,
        english_transcript=english_transcript,
        detected_language=detected_language,
        key_points=key_points,
        visual_context=visual_context,
    )

    output_path = (
        OUTPUT_JSON_DIR /
        "video_intelligence.json"
    )

    with open(
        output_path,
        "w",
        encoding="utf-8",
    ) as output_file:

        json.dump(
            final_data,
            output_file,
            indent=4,
            ensure_ascii=False,
        )

    logger.info(
        "=" * 60
    )

    logger.info(
        "PHASE 1 COMPLETE"
    )

    logger.info(
        "Output: %s",
        output_path,
    )

    logger.info(
        "Transcript segments: %d",
        len(transcript_segments),
    )

    logger.info(
        "Key points: %d",
        len(key_points),
    )

    logger.info(
        "Visual contexts: %d",
        len(visual_context),
    )

    logger.info(
        "=" * 60
    )

    return output_path


# ============================================================
# CLI
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "VisionPR Phase 1 - "
            "Multimodal Intelligence Extraction"
        )
    )

    parser.add_argument(
        "--video",
        required=True,
        help=(
            "Path to the input meeting video."
        ),
    )

    args = parser.parse_args()

    video_path = Path(
        args.video
    ).resolve()

    try:

        output_path = process_video(
            video_path
        )

        print(
            "\nPhase 1 completed successfully."
        )

        print(
            f"video_intelligence.json: "
            f"{output_path}"
        )

    except KeyboardInterrupt:

        logger.error(
            "Pipeline interrupted by user."
        )

        sys.exit(130)

    except Exception as exc:

        logger.exception(
            "Phase 1 failed: %s",
            exc,
        )

        sys.exit(1)


if __name__ == "__main__":
    main()