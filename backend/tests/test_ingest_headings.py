"""Unit tests for heading-path extraction (no GCP / Supabase calls)."""
from app.ingest_headings import extract_heading_path


def test_clean_prepended_chain_single():
    content = (
        "# 1.3 ZUSÄTZLICHE ANGABEN BEI BIETERGEMEINSCHAFTEN / SUBUNTERNEHMERN\n"
        "\n"
        "Die Bietergemeinschaft hat ihre Angaben gemäss …\n"
    )
    assert extract_heading_path(content) == [
        "1.3 ZUSÄTZLICHE ANGABEN BEI BIETERGEMEINSCHAFTEN / SUBUNTERNEHMERN",
    ]


def test_multi_level_chain_preserves_order():
    content = "# A\n## 1\n### 1.1\n\nbody text\n"
    assert extract_heading_path(content) == ["A", "1", "1.1"]


def test_multi_level_chain_with_blank_lines_between_headings():
    # Document AI's actual prepended format separates headings with blank
    # lines: "# H1\n\n## H2\n\n### H3\n\n<body>". The peel must skip them.
    content = (
        "# VERFAHRENSBESTIMMUNGEN TEIL A\n"
        "\n"
        "## 1 AUFTRAGGEBER\n"
        "\n"
        "### 1.1 OFFIZIELLER NAME UND ADRESSE DES AUFTRAGGEBERS\n"
        "\n"
        "Bedarfsstelle / Vergabestelle Einwohnergemeinde Hochdorf …\n"
    )
    assert extract_heading_path(content) == [
        "VERFAHRENSBESTIMMUNGEN TEIL A",
        "1 AUFTRAGGEBER",
        "1.1 OFFIZIELLER NAME UND ADRESSE DES AUFTRAGGEBERS",
    ]


def test_run_on_recovery_picks_up_inline_marker():
    content = (
        "### 1.2 EINGABESTELLE\n"
        "\n"
        "Postadresse: Gemeindeverwaltung, 6280 Hochdorf1.3 FRAGENDie "
        "Fragen sind unter www.simap.ch einzureichen.\n"
    )
    out = extract_heading_path(content)
    assert out is not None
    assert "1.2 EINGABESTELLE" in out
    assert "1.3 FRAGEN" in out
    # Order: leading heading first, inline recovery after.
    assert out.index("1.2 EINGABESTELLE") < out.index("1.3 FRAGEN")


def test_body_only_chunk_returns_none():
    content = "Just some paragraph text with no heading and no markers.\n"
    assert extract_heading_path(content) is None


def test_empty_input_returns_none():
    assert extract_heading_path(None) is None
    assert extract_heading_path("") is None
    assert extract_heading_path("   \n  \n") is None


def test_decimal_in_body_is_not_a_section():
    content = (
        "# Vorbemerkungen\n"
        "\n"
        "Der Auftraggeber hat page 1.3 mio CHF im Budget. Die Messung "
        "ergab Tel. 31.3 sec Verzögerung.\n"
    )
    out = extract_heading_path(content)
    assert out == ["Vorbemerkungen"]


def test_dedup_between_path_a_and_path_b():
    content = (
        "# 1.3 FRAGEN\n"
        "\n"
        "Wie in 1.3 FRAGEN beschrieben, sind alle Fragen unter www.simap.ch "
        "einzureichen.\n"
    )
    out = extract_heading_path(content)
    assert out == ["1.3 FRAGEN"]


def test_inline_marker_with_camelcase_boundary():
    # FRAGENDie → must split at the FRAGEN|D boundary, not include "Die".
    content = "Vorher1.3 FRAGENDie Fragen folgen."
    out = extract_heading_path(content)
    assert out == ["1.3 FRAGEN"]


def test_multiple_inline_markers_in_order():
    content = (
        "Body text 1.2 EINGABESTELLE then more text 1.3 FRAGEN and then "
        "1.4 EINREICHUNG DES ANGEBOTES at the end.\n"
    )
    out = extract_heading_path(content)
    assert out == [
        "1.2 EINGABESTELLE",
        "1.3 FRAGEN",
        "1.4 EINREICHUNG DES ANGEBOTES",
    ]


def test_inline_marker_requires_at_least_one_dot():
    # Single-digit "5 SOMETHING" should not match — too noisy, and almost
    # always shows up as a proper markdown heading anyway.
    content = "Body 5 SOMETHING here."
    assert extract_heading_path(content) is None


def test_three_level_inline_marker():
    content = "See section 3.4.2 ANFORDERUNGEN AN DAS BAUWERK.\n"
    out = extract_heading_path(content)
    assert out == ["3.4.2 ANFORDERUNGEN AN DAS BAUWERK"]
