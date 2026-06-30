import re
from typing import Any

from youtube_transcript_api import YouTubeTranscriptApi

from app.storage.documents import DocumentStore

YOUTUBE_PATTERNS = [
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})",
    r"youtube\.com/shorts/([a-zA-Z0-9_-]{11})",
]


def extract_video_id(url: str) -> str | None:
    for pattern in YOUTUBE_PATTERNS:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def _format_transcript(segments: list[dict]) -> str:
    lines = []
    for seg in segments:
        start = seg.get("start", 0)
        text = seg.get("text", "").strip()
        if text:
            minutes = int(start // 60)
            seconds = int(start % 60)
            lines.append(f"[{minutes:02d}:{seconds:02d}] {text}")
    return "\n".join(lines)


async def ingest_youtube(
    store: DocumentStore,
    *,
    url: str,
    title: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    video_id = extract_video_id(url)
    if not video_id:
        raise ValueError("Could not extract YouTube video ID from URL")

    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        transcript = transcript_list.find_transcript(["en"])
        try:
            transcript = transcript.translate("en")
        except Exception:
            pass
        segments = transcript.fetch()
    except Exception as exc:
        raise ValueError(
            f"No transcript available for this video ({video_id}). "
            "Captions may be disabled or unavailable."
        ) from exc

    text = _format_transcript(segments)
    meta = dict(metadata or {})
    meta.update({"video_id": video_id, "source_url": url})

    return await store.create_document(
        title=title or f"YouTube: {video_id}",
        source_type="youtube",
        source_uri=url,
        extracted_text=text,
        metadata=meta,
    )
