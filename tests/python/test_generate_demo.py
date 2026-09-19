from pathlib import Path

import pytest
from scripts.generate_demo import ROOT, _output_path


def test_demo_output_is_confined_beneath_public() -> None:
    assert _output_path("public/demo") == (ROOT / "public/demo").resolve()
    for value in ("/", ".", "ingest", "public", "../outside"):
        with pytest.raises(ValueError, match="beneath public"):
            _output_path(value)


def test_demo_output_rejects_symlink_escape(tmp_path: Path) -> None:
    link = ROOT / "public" / ".test-outside-link"
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
        with pytest.raises(ValueError, match="beneath public"):
            _output_path("public/.test-outside-link/demo")
    finally:
        link.unlink(missing_ok=True)
