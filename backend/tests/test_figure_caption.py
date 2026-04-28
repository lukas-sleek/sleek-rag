"""Unit tests for figure label / caption extraction."""
from app.figure_caption import extract_figure_caption, extract_figure_label


def test_label_at_start_english():
    assert extract_figure_label("Figure 3.6: Hydraulic diagram") == "Figure 3.6"


def test_label_at_start_german():
    assert extract_figure_label("Abbildung 2.1 Diagramm") == "Abbildung 2.1"


def test_label_short_german_with_period():
    assert extract_figure_label("Abb. 5 caption") == "Abb 5"


def test_label_buried_in_long_content():
    # Mirrors the real Teil B case — the figure caption appears after the
    # LLM-generated annotation block, not at the start of the chunk.
    content = (
        "# 1 AUSGANGSLAGE\n\n## 2.1 PROJEKTPERIMETER\n\n"
        "The image displays a topographic map …\n\n"
        "Abbildung 1: Darstellung des Projektperimeters\n"
    )
    assert extract_figure_label(content) == "Abbildung 1"


def test_label_returns_none_when_no_match():
    assert extract_figure_label("Random body text") is None
    assert extract_figure_label(None) is None
    assert extract_figure_label("") is None


def test_caption_includes_descriptive_title():
    content = (
        "long llm annotation here …\n\n"
        "Abbildung 1: Darstellung des Projektperimeters\n"
    )
    assert (
        extract_figure_caption(content)
        == "Abbildung 1: Darstellung des Projektperimeters"
    )


def test_caption_falls_back_to_label_when_no_title():
    assert extract_figure_caption("Abbildung 5") == "Abbildung 5"


def test_caption_handles_quelle_annotation():
    content = (
        "Abbildung 2: Grundstruktur hinsichtlich Städtebau und Freiraum "
        "(Quelle: Masterplan Südiareal)"
    )
    assert extract_figure_caption(content) == (
        "Abbildung 2: Grundstruktur hinsichtlich Städtebau und Freiraum "
        "(Quelle: Masterplan Südiareal)"
    )


def test_caption_truncates_at_newline():
    content = "Abbildung 3: Erste Zeile\nweitere Beschreibung folgt …"
    assert extract_figure_caption(content) == "Abbildung 3: Erste Zeile"


def test_caption_returns_none_when_no_match():
    assert extract_figure_caption("body without any figure label") is None
    assert extract_figure_caption(None) is None
    assert extract_figure_caption("") is None


def test_label_does_not_match_in_middle_of_word():
    # "labbildung" should not match — \b requires a word boundary before.
    assert extract_figure_label("labbildung 5") is None
