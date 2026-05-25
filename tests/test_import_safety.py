"""Verify import/curation pipelines are copy-only by default."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

IMPORT_FLOW_FILES = (
    "import_subject_media.py",
    "media_import_safety.py",
)

DESTRUCTIVE_CALL_PATTERNS = (
    "shutil.move(",
    "shutil.rmtree(",
    "os.remove(",
    ".unlink(",
)


def _read(rel_path: str) -> str:
    path = PROJECT_ROOT / rel_path
    assert path.is_file(), f"Missing expected file: {path}"
    return path.read_text(encoding="utf-8")


def test_import_scripts_have_no_destructive_ops():
    for name in IMPORT_FLOW_FILES:
        text = _read(name)
        for pattern in DESTRUCTIVE_CALL_PATTERNS:
            assert pattern not in text, f"{name} must not contain {pattern!r}"


def test_vip_curator_move_is_opt_in_only():
    text = _read("auto_vip_curator_s2.py")
    assert "move_rejects: bool = False" in text
    assert "--move-rejects" in text
    assert "--copy-rejects-to" in text
    assert 'CLEANUP_STRATEGY = "none_by_default"' in text
    assert "_copy_to_scarti" in text
    assert "[SAFE] No reject cleanup" in text


def test_auto_curator_mirror_uses_copy_only():
    text = _read("auto_curator.py")
    start = text.index("def _mirror_test_faces")
    end = text.index("\ndef run_batch_curation", start)
    section = text[start:end]
    assert "shutil.copy2" in section
    assert "shutil.move" not in section
    assert "SAFETY" in section


def test_import_subject_media_logs_safe_mode():
    text = _read("import_subject_media.py")
    assert "SAFETY_GUARD = True" in text
    assert "[SAFE] copy-only mode — source untouched, no deletions" in text
    assert "--allow-destructive" not in text
    assert "shutil.move(" not in text


def test_import_dry_run_from_scarti_smoke():
    """Dry-run import using scarti as fake external source (no writes)."""
    from import_subject_media import run_subject_import

    scarti = PROJECT_ROOT / "inputs" / "scarti_soggetto2"
    if not scarti.is_dir():
        return

    stats = run_subject_import(
        scarti,
        subject_id=2,
        copy_to_inputs=False,
        dry_run=True,
        interactive=False,
    )
    assert stats.media_scanned >= 0


if __name__ == "__main__":
    for _name in sorted(n for n in globals() if n.startswith("test_")):
        globals()[_name]()
        print("OK", _name)
    print("ALL PASSED")
