"""Admin-only pack/restore of the persistent data directory."""

from __future__ import annotations

import tarfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.config import settings

SKIP_NAMES = {".DS_Store", "__pycache__"}
PACK_PREFIX = "bright-health-data/"


def data_root() -> Path:
    path = Path(settings.data_dir).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _skip(path: Path) -> bool:
    return path.name in SKIP_NAMES or path.suffix == ".pyc"


def iter_data_files() -> Iterator[Path]:
    root = data_root()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or _skip(path):
            continue
        yield path


def data_pack_status() -> dict[str, Any]:
    from app.services.case_manager import list_patients

    files = list(iter_data_files())
    patients = []
    for patient in list_patients():
        patients.append(
            {
                "id": str(patient.get("id") or ""),
                "label": str(patient.get("label") or patient.get("id") or ""),
                "cases": len(patient.get("cases") or []),
            }
        )
    return {
        "files": len(files),
        "bytes": sum(path.stat().st_size for path in files),
        "patients": patients,
    }


def write_data_pack(dest: Path) -> Path:
    root = data_root()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(dest, "w:gz") as tar:
        for path in iter_data_files():
            rel = path.relative_to(root).as_posix()
            tar.add(path, arcname=f"{PACK_PREFIX}{rel}")
    return dest


def _safe_member(member: tarfile.TarInfo, dest_root: Path) -> Path | None:
    name = member.name.replace("\\", "/")
    if member.issym() or member.islnk():
        return None
    if name.startswith("/") or name.startswith(".."):
        return None
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    if parts[0] == PACK_PREFIX.rstrip("/"):
        parts = parts[1:]
    if not parts:
        return None
    target = (dest_root.joinpath(*parts)).resolve()
    if not str(target).startswith(str(dest_root.resolve()) + "/") and target != dest_root.resolve():
        return None
    if target.name in SKIP_NAMES:
        return None
    return target


def restore_data_pack(archive: Path) -> dict[str, Any]:
    dest_root = data_root()
    extracted = 0
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            target = _safe_member(member, dest_root)
            if target is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is None:
                continue
            try:
                target.write_bytes(source.read())
            finally:
                source.close()
            extracted += 1
    return {"extracted": extracted, **data_pack_status()}
