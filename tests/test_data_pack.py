"""Admin data-pack export/restore stays inside the data directory."""

from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.services.data_pack import data_pack_status, restore_data_pack, write_data_pack


def test_pack_and_restore_round_trip(tmp_path, monkeypatch):
    src = tmp_path / "old"
    dest = tmp_path / "new"
    src.mkdir()
    (src / "registry.json").write_text('{"patients":[{"id":"p1","label":"Pat"}]}', encoding="utf-8")
    (src / "patients" / "p1").mkdir(parents=True)
    (src / "patients" / "p1" / "profile.json").write_text('{"journal":[]}', encoding="utf-8")
    (src / "patients" / "p1" / "secret.txt").write_text("keep", encoding="utf-8")

    monkeypatch.setattr(settings, "data_dir", src)
    archive = write_data_pack(tmp_path / "pack.tgz")
    status = data_pack_status()
    assert status["files"] >= 3
    assert archive.stat().st_size > 20

    monkeypatch.setattr(settings, "data_dir", dest)
    dest.mkdir()
    result = restore_data_pack(archive)
    assert (dest / "registry.json").read_text(encoding="utf-8").find("p1") >= 0
    assert (dest / "patients" / "p1" / "secret.txt").read_text(encoding="utf-8") == "keep"
    assert result["extracted"] >= 3


def test_restore_rejects_path_escape(tmp_path, monkeypatch):
    import tarfile

    dest = tmp_path / "dest"
    dest.mkdir()
    archive = tmp_path / "evil.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        evil = tmp_path / "payload.txt"
        evil.write_text("nope", encoding="utf-8")
        info = tar.gettarinfo(evil)
        info.name = "../outside.txt"
        with evil.open("rb") as fh:
            tar.addfile(info, fh)
    monkeypatch.setattr(settings, "data_dir", dest)
    restore_data_pack(archive)
    assert not (tmp_path / "outside.txt").exists()
