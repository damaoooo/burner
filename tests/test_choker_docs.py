from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_choker_requirements_document_records_core_contract():
    text = (ROOT / "docs" / "choker.md").read_text(encoding="utf-8")

    assert "python -m choker <command>" in text
    assert "`start`" in text
    assert "`stop`" in text
    assert "`status`" in text
    assert "`run`" in text
    assert "`--threshold`" in text
    assert "`--strategy`" in text
    assert "`--target`" in text
    assert "`--window-ms`" in text
    assert "0..100" in text
    assert "external_cpu = max(0, total_cpu - choker_owned_cpu)" in text
    assert "Version 1 is CPU-only" in text
    assert "sine" in text
