import asyncio
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.storage.documents import DocumentStore

FACEBOOK_HOST_SUFFIXES = ("facebook.com", "fb.com", "fb.watch")


def is_facebook_video_url(url: str) -> bool:
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if not host:
        return False
    if host == "fb.watch":
        return True
    if not any(host == suffix or host.endswith(f".{suffix}") for suffix in FACEBOOK_HOST_SUFFIXES):
        return False
    path = (parsed.path or "").lower()
    query = parsed.query or ""
    if "/reel/" in path or "/reels/" in path:
        return True
    if "/videos/" in path or "/video.php" in path:
        return True
    if "/watch" in path or "v=" in query:
        return True
    return False


def _srt_to_text(content: str) -> str:
    blocks = re.split(r"\n\s*\n", content.strip())
    lines: list[str] = []
    for block in blocks:
        parts = [line.strip() for line in block.splitlines() if line.strip()]
        if not parts:
            continue
        text_parts = [
            part
            for part in parts
            if not part.isdigit() and "-->" not in part and not part.upper().startswith("WEBVTT")
        ]
        if text_parts:
            lines.append(" ".join(text_parts))
    return "\n".join(lines)


def _vtt_to_text(content: str) -> str:
    lines: list[str] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.upper() == "WEBVTT":
            continue
        if line.isdigit() or "-->" in line or line.startswith("NOTE"):
            continue
        lines.append(line)
    return "\n".join(lines)


def _subtitle_file_to_text(path: Path) -> str:
    content = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() == ".vtt":
        return _vtt_to_text(content)
    return _srt_to_text(content)


def _build_extracted_text(
    *,
    description: str,
    transcript: str,
    notes: str | None,
    source_url: str,
) -> str:
    sections: list[str] = []
    if description.strip():
        sections.append(f"=== Description ===\n{description.strip()}")
    if transcript.strip():
        sections.append(f"=== Transcript ===\n{transcript.strip()}")
    elif notes and notes.strip():
        pass
    else:
        sections.append(
            "=== Transcript ===\n"
            "[No automatic captions were available for this Facebook video. "
            "Add notes below or rely on the stored video file.]"
        )
    if notes and notes.strip():
        sections.append(f"=== Notes ===\n{notes.strip()}")
    sections.append(f"Source: {source_url}")
    return "\n\n".join(sections)


def _download_facebook_video(url: str, tmpdir: Path) -> dict[str, Any]:
    import yt_dlp

    outtmpl = str(tmpdir / "video.%(ext)s")
    ydl_opts = {
        "format": "best[ext=mp4]/best",
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en", "en-US", "en-GB", "en_US"],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    video_path = next((p for p in tmpdir.iterdir() if p.is_file() and p.suffix.lower() == ".mp4"), None)
    if not video_path:
        video_path = next(
            (
                p
                for p in tmpdir.iterdir()
                if p.is_file() and p.suffix.lower() in {".mov", ".webm", ".mkv", ".m4v"}
            ),
            None,
        )
    if not video_path:
        raise ValueError("Facebook video download failed — no video file was saved.")

    transcript = ""
    for sub_path in sorted(tmpdir.glob("video.*")):
        if sub_path.suffix.lower() in {".srt", ".vtt"} and sub_path.is_file():
            transcript = _subtitle_file_to_text(sub_path)
            if transcript.strip():
                break

    return {
        "info": info or {},
        "video_path": video_path,
        "transcript": transcript,
    }


async def ingest_facebook(
    store: DocumentStore,
    *,
    url: str,
    title: str | None = None,
    notes: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_url = url.strip()
    if not is_facebook_video_url(normalized_url):
        raise ValueError(
            "Unsupported Facebook URL. Use a public reel, watch, or fb.watch link."
        )

    with tempfile.TemporaryDirectory(prefix="beatit-fb-") as tmp:
        tmpdir = Path(tmp)
        try:
            result = await asyncio.to_thread(_download_facebook_video, normalized_url, tmpdir)
        except Exception as exc:
            message = str(exc).strip() or exc.__class__.__name__
            if "private" in message.lower() or "login" in message.lower():
                raise ValueError(
                    "Could not access this Facebook video. It may be private, "
                    "login-only, or blocked from download."
                ) from exc
            raise ValueError(f"Could not download Facebook video: {message}") from exc

        info = result["info"]
        video_path: Path = result["video_path"]
        transcript = result.get("transcript") or ""
        video_id = str(info.get("id") or video_path.stem)
        description = str(info.get("description") or info.get("title") or "").strip()
        content = video_path.read_bytes()
        if not content:
            raise ValueError("Downloaded Facebook video file was empty.")

        extracted = _build_extracted_text(
            description=description,
            transcript=transcript,
            notes=notes,
            source_url=normalized_url,
        )

        meta = dict(metadata or {})
        meta.update(
            {
                "facebook_video_id": video_id,
                "source_url": normalized_url,
                "original_filename": f"facebook-{video_id}{video_path.suffix.lower()}",
                "file_extension": video_path.suffix.lower(),
                "transcription_status": "captions" if transcript.strip() else "none",
                "platform": "facebook",
            }
        )
        if info.get("uploader"):
            meta["uploader"] = info.get("uploader")
        if info.get("duration"):
            meta["duration_seconds"] = info.get("duration")

        display_title = title or description or f"Facebook reel {video_id}"
        if len(display_title) > 180:
            display_title = display_title[:177] + "..."

        return await store.create_document(
            title=display_title,
            source_type="facebook",
            source_uri=normalized_url,
            extracted_text=extracted,
            raw_filename=meta["original_filename"],
            raw_content=content,
            metadata=meta,
        )
