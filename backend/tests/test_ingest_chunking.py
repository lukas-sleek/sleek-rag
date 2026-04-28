"""Unit tests for the ingest helpers — no GCP / Supabase calls."""
from app.figure_caption import extract_figure_label as _extract_figure_label


def test_extract_figure_label_english():
    assert _extract_figure_label("Figure 3.6: Hydraulic diagram") == "Figure 3.6"


def test_extract_figure_label_german():
    assert _extract_figure_label("Abbildung 2.1 Diagramm") == "Abbildung 2.1"


def test_extract_figure_label_short_german():
    assert _extract_figure_label("Abb. 5 caption") == "Abb 5"


def test_extract_figure_label_none_when_no_match():
    assert _extract_figure_label("Random body text") is None


def test_extract_figure_label_handles_none():
    assert _extract_figure_label(None) is None
    assert _extract_figure_label("") is None
