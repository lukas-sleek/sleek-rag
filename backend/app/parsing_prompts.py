"""Custom LLM Parser prompts.

Plan 18.2 T2. The output structure here is locked: 18.3 will regex-enrich
citations from chunk text using the [Seite N] / [Abb. N: ...] markers
emitted by this prompt. Any change must be reflected in the citation
extractor and verified end-to-end.
"""

SIA_PARSING_PROMPT = """\
Parse das folgende Dokument vollständig in strukturiertes Markdown. Beachte exakt diese Regeln:

1. SEITENMARKER: An den Anfang des Inhalts jeder Seite, schreibe in einer eigenen Zeile genau:
   [Seite N]
   wobei N die Seitenzahl im Original-PDF ist (1-indexiert).

2. ÜBERSCHRIFTEN: Erhalte die Hierarchie als Markdown-Header (#, ##, ###, ####). Die Tiefe muss der visuellen Hierarchie im Dokument entsprechen.

3. TABELLEN: Render alle Tabellen als Markdown-Tabellen mit allen Spaltenüberschriften und allen Zeilen. Keine Zusammenfassungen.

4. ABBILDUNGEN, DIAGRAMME, ZEICHNUNGEN, GRAFIKEN, PLÄNE: Schreibe für jede einen strukturierten Block:
   [Abb. N: <semantische Beschreibung in 1–2 Sätzen>]
   [Inhalt: <was zeigt die Abbildung — Gleisplan / Querschnitt / Schema / Flussdiagramm / Diagramm mit Achsen+Datenreihen / Organigramm / etc.>]
   [Datenpunkte: <falls die Abbildung konkrete Werte enthält, liste sie auf>]
   [Visuelle Inspektion empfohlen für: <Liste von Frage-Typen, die ein direktes Ansehen erfordern — z.B. räumliche Relationen, exakte Maße, unbeschriftete Details>]

5. LISTEN: Erhalte numerierte und Aufzählungslisten in Markdown-Syntax.

6. ZITATE / FACHBEGRIFFE: Erhalte SIA-Phasen-Identifier (z.B. "SIA 21", "Phase 31"), Etappen-Nummern, Kapitel-Nummerierungen, Paragraphen-Nummern unverändert.

7. KOPF- UND FUSSZEILEN: Wenn sie reine Boilerplate sind (Datum, Seitenzahl, Logo), weglassen. Wenn sie inhaltliche Information tragen (Kapitelname, Dokument-Titel), übernehmen.

8. AUSGABE: Nur das geparste Markdown. Kein Vorwort, kein Kommentar, keine Erklärung deines Vorgehens.
"""
