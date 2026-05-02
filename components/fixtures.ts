// All static data + helpers shared across components.

export type Chat = { id: string; title: string };
export type Project = {
  id: string;
  name: string;
  expanded: boolean;
  hasFiles: boolean;
  chats: Chat[];
};

export const PROJECTS_INITIAL: Project[] = [
  {
    id: "p-b",
    name: "Project B",
    expanded: true,
    hasFiles: true,
    chats: [
      { id: "c-b1", title: "Test 2" },
      { id: "c-b2", title: "Test 1" },
    ],
  },
  {
    id: "p-a",
    name: "Project A",
    expanded: true,
    hasFiles: true,
    chats: [
      { id: "c-a1", title: "ProjektAnalyse" },
      { id: "c-a2", title: "Fragen" },
      { id: "c-a3", title: "Test" },
    ],
  },
];

// Citation discriminator. Plan 19.0 adds web citations alongside the
// existing file (Vertex RAG) chunks. Older rows (pre-19.0) have kind
// undefined — treat as "file" for back-compat.
export type Citation = {
  chunk_id: string;
  // "file" or undefined (legacy) for project-document chunks; "web" for
  // results parsed from web_researcher's mandated Quellen block.
  kind?: "file" | "web";
  // file fields
  project_id?: string | null;
  file_id?: string | null;
  filename: string;
  snippet: string;
  score: number | null;
  // web fields (only present when kind === "web")
  url?: string | null;
  title?: string | null;
  domain?: string | null;
};

// One step in the agent trace surfaced for debug accounts. The backend emits
// these alongside `delta` / `meta` / `done` SSE frames; the activity panel
// in `Message` renders them as a collapsible reasoning view. Production
// users never see traces — frames are gated server-side by user email.
export type TraceStep = {
  id: string;
  author: string;             // agent name, e.g. "chat_orchestrator"
  kind: "tool_call" | "tool_response" | "model_text";
  name?: string | null;       // tool name on tool_call / tool_response
  args?: string | null;       // truncated JSON of tool_call args
  response?: string | null;   // truncated JSON of tool_response body
  text?: string | null;       // truncated model text
};

export type Message = {
  role: "user" | "assistant";
  content: string;
  id?: string;
  citations?: Citation[] | null;
  traces?: TraceStep[] | null;
};

export const SAMPLE_THREAD: Record<string, Message[]> = {
  "c-b1": [
    {
      role: "user",
      content:
        "Can you summarize the key findings from the Q1 project analysis and flag anything that needs follow-up?",
    },
    {
      role: "assistant",
      content:
        "Here's a structured summary of the Q1 ProjektAnalyse:\n\n1. Throughput is up 12% over Q4, driven mainly by the new pipeline rollout in February.\n2. Two regions (DE-South, AT-East) are still below baseline — both flagged for next-quarter review.\n3. The vendor migration is on schedule but slightly over budget (≈4%).\n\nThings that need follow-up:\n• Sign-off from procurement on the revised contract terms.\n• A decision on whether to extend the pilot to Project B.\n• Updated risk register — last entry is from Feb 14.\n\nWant me to draft the follow-up email or open a ticket for procurement?",
    },
    {
      role: "user",
      content: "Draft the follow-up email — keep it under 120 words.",
    },
  ],
};

// ---- Files ---------------------------------------------------------------

export type FileEntity = { text: string; type: string; confidence: number };
export type FileAnalysis = {
  summary: string;
  entities: FileEntity[];
  keyStats: { label: string; value: string }[];
  recommendations?: string[];
};

export type FileDetail = {
  id: string;
  filename: string;
  size_bytes: number | null;
  mime_type: string | null;
  page_count: number | null;
  status: string;
  ingest_error: string | null;
  created_at: string | null;
};

export type FileItem = {
  id: string;
  name: string;
  size: string;
  type: "pdf" | "docx" | "csv" | "image";
  pages: number;
  status: "complete" | "analyzing" | "failed";
  // Raw backend status — drives the granular label in the file modal.
  // 'queued' | 'parsing' | 'ready' | 'failed' for new files;
  // 'pending' | 'indexed' for legacy rows.
  ingestStatus?: string;
  ingestError?: string | null;
  analysis: FileAnalysis | null;
};

export const SAMPLE_FILES: FileItem[] = [
  {
    id: "f1",
    name: "Q4-2025-Umsatzbericht.pdf",
    size: "2.4 MB",
    type: "pdf",
    pages: 42,
    status: "complete",
    analysis: {
      summary:
        "Quartalsbericht zur Umsatzentwicklung Q4 2025 über drei Geschäftsbereiche: SaaS-Plattform (12,8 Mio. €), Professional Services (4,2 Mio. €) und Enterprise Licensing (8,6 Mio. €). Gesamtumsatz 25,6 Mio. € — +23 % YoY, getragen von Enterprise-Upselling und verbesserter Net Revenue Retention.",
      entities: [
        { text: "SaaS-Plattform", type: "Geschäftsbereich", confidence: 0.98 },
        { text: "25,6 Mio. €", type: "Umsatz", confidence: 0.99 },
        { text: "+23 % YoY", type: "Wachstum", confidence: 0.97 },
        { text: "134 % NRR", type: "Kennzahl", confidence: 0.95 },
        { text: "Q4 2025", type: "Zeitraum", confidence: 0.99 },
        { text: "2,4 % Churn", type: "Kennzahl", confidence: 0.93 },
        { text: "78,3 % Bruttomarge", type: "Kennzahl", confidence: 0.96 },
      ],
      keyStats: [
        { label: "Umsatz", value: "25,6 Mio. €" },
        { label: "Wachstum", value: "+23 %" },
        { label: "NRR", value: "134 %" },
        { label: "Bruttomarge", value: "78,3 %" },
        { label: "Churn", value: "2,4 %" },
        { label: "Seiten", value: "42" },
      ],
      recommendations: [
        "Mid-Tier-Plan zwischen Starter und Pro einführen, um SMB-Churn unter 10 K € ARR zu adressieren",
        "20 % mehr Ressourcen für Solutions Engineering basierend auf 3,2× ROI",
        "Content-Marketing-Team ausbauen — organische Akquise = 40 % des Neuumsatzes bei einem Fünftel der CAC",
      ],
    },
  },
  {
    id: "f2",
    name: "Mitarbeiter-Umfrage-2026.csv",
    size: "840 KB",
    type: "csv",
    pages: 1,
    status: "complete",
    analysis: {
      summary:
        "Mitarbeiterzufriedenheitsumfrage März 2026 mit 847 Antworten aus 12 Abteilungen. Gesamtscore 4,2/5,0. Hauptanliegen: Karrierechancen (3,1/5) und teamübergreifende Zusammenarbeit (3,4/5). Remote-Arbeit erhielt mit 4,7/5 die höchste Bewertung.",
      entities: [
        { text: "847 Antworten", type: "Stichprobe", confidence: 0.99 },
        { text: "4,2/5,0", type: "Score", confidence: 0.98 },
        { text: "12 Abteilungen", type: "Umfang", confidence: 0.97 },
        { text: "3,1/5 Karriere", type: "Score", confidence: 0.95 },
        { text: "4,7/5 Remote", type: "Score", confidence: 0.96 },
      ],
      keyStats: [
        { label: "Antworten", value: "847" },
        { label: "Score", value: "4,2/5" },
        { label: "Abteilungen", value: "12" },
        { label: "Rücklauf", value: "78 %" },
        { label: "Niedrigster", value: "Karriere" },
        { label: "Höchster", value: "Remote" },
      ],
      recommendations: [
        "Strukturiertes Mentorenprogramm einführen, um Karrierechancen zu adressieren",
        "Quartalsweise teamübergreifende Projektrotationen einführen",
        "Folgegespräche mit Abteilungen unter 3,5 Score",
      ],
    },
  },
  {
    id: "f3",
    name: "Architektur-Diagramm-v3.png",
    size: "1.8 MB",
    type: "image",
    pages: 1,
    status: "complete",
    analysis: {
      summary:
        "Systemarchitektur-Diagramm mit Microservices-Deployment auf AWS. Identifiziert 8 Services über API Gateway, Redis-Caching, PostgreSQL-Primärdatenbank und S3 für Object Storage. Hub-and-Spoke-Topologie mit API Gateway als zentralem Knoten.",
      entities: [
        { text: "API Gateway", type: "Komponente", confidence: 0.96 },
        { text: "PostgreSQL", type: "Datenbank", confidence: 0.98 },
        { text: "Redis", type: "Cache", confidence: 0.97 },
        { text: "AWS", type: "Cloud", confidence: 0.99 },
        { text: "S3", type: "Storage", confidence: 0.98 },
        { text: "8 Microservices", type: "Architektur", confidence: 0.92 },
      ],
      keyStats: [
        { label: "Services", value: "8" },
        { label: "Datenbanken", value: "2" },
        { label: "Cloud", value: "AWS" },
        { label: "Topologie", value: "Hub-Spoke" },
        { label: "Cache", value: "Redis" },
        { label: "Storage", value: "S3" },
      ],
      recommendations: [
        "Circuit Breaker zwischen API Gateway und nachgelagerten Services hinzufügen",
        "Read Replicas für PostgreSQL angesichts der Microservices-Lesezugriffe prüfen",
        "Service-Abhängigkeiten und Failure Modes je Verbindung dokumentieren",
      ],
    },
  },
  {
    id: "f4",
    name: "Lieferantenvertrag-Entwurf.docx",
    size: "520 KB",
    type: "docx",
    pages: 18,
    status: "complete",
    analysis: {
      summary:
        "Entwurf eines Rahmenliefervertrags mit einem strategischen Komponentenlieferanten. Laufzeit 36 Monate, Mindestabnahme 1,2 Mio. €/Jahr, Zahlungsziel 60 Tage. Enthält Klauseln zu Qualitätssicherung (ISO 9001), Lieferverzug (0,5 % pro Woche, max. 5 %) und einseitige Preisanpassungsrechte des Lieferanten bei Rohstoffschwankungen ab 8 %.",
      entities: [
        { text: "36 Monate", type: "Zeitraum", confidence: 0.98 },
        { text: "1,2 Mio. €/Jahr", type: "Umsatz", confidence: 0.97 },
        { text: "60 Tage", type: "Kennzahl", confidence: 0.96 },
        { text: "ISO 9001", type: "Komponente", confidence: 0.99 },
        { text: "0,5 %/Woche", type: "Kennzahl", confidence: 0.95 },
        { text: "Preisanpassung 8 %", type: "Kennzahl", confidence: 0.93 },
      ],
      keyStats: [
        { label: "Laufzeit", value: "36 Mt." },
        { label: "Mindestabnahme", value: "1,2 Mio. €" },
        { label: "Zahlungsziel", value: "60 Tage" },
        { label: "Pönale", value: "0,5 %/Wo." },
        { label: "Klauseln", value: "27" },
        { label: "Seiten", value: "18" },
      ],
      recommendations: [
        "Einseitiges Preisanpassungsrecht ab 8 % auf gegenseitige Verhandlungspflicht zurückführen",
        "Pönale-Cap von 5 % auf 10 % anheben, kombiniert mit Rücktrittsrecht ab 4 Wochen Verzug",
        "Zahlungsziel von 60 auf 45 Tage reduzieren, um Bonus von 1,5 % Skonto zu sichern",
      ],
    },
  },
];

export const PROJECT_B_FILES: FileItem[] = [
  {
    id: "fb1",
    name: "DSGVO-Audit-2026.pdf",
    size: "3.1 MB",
    type: "pdf",
    pages: 56,
    status: "complete",
    analysis: {
      summary:
        "Internes DSGVO-Audit Q1 2026 über 14 Verarbeitungstätigkeiten. 3 kritische Befunde (Auftragsverarbeitung Cloud-Anbieter, Löschkonzept Kundendaten, Mitarbeiter-Tracking-Tools), 8 mittlere und 11 geringfügige Abweichungen. Reifegrad 2,8/5,0 — Verbesserung gegenüber Vorjahr (2,3).",
      entities: [
        { text: "14 Verarbeitungen", type: "Umfang", confidence: 0.98 },
        { text: "3 kritische Befunde", type: "Kennzahl", confidence: 0.99 },
        { text: "Reifegrad 2,8/5,0", type: "Score", confidence: 0.97 },
        { text: "Cloud-Anbieter", type: "Komponente", confidence: 0.95 },
        { text: "Q1 2026", type: "Zeitraum", confidence: 0.99 },
        { text: "Löschkonzept", type: "Geschäftsbereich", confidence: 0.94 },
      ],
      keyStats: [
        { label: "Befunde", value: "22" },
        { label: "Kritisch", value: "3" },
        { label: "Reifegrad", value: "2,8/5" },
        { label: "Vorjahr", value: "2,3/5" },
        { label: "Verfahren", value: "14" },
        { label: "Seiten", value: "56" },
      ],
      recommendations: [
        "Auftragsverarbeitungsverträge mit allen Cloud-Anbietern bis Q2 neu verhandeln",
        "Automatisiertes Löschkonzept für Kundendaten >7 Jahre einführen",
        "Mitarbeiter-Tracking-Tools mit Betriebsrat überprüfen und einschränken",
      ],
    },
  },
  {
    id: "fb2",
    name: "Risikoregister-2026.xlsx",
    size: "1.2 MB",
    type: "csv",
    pages: 1,
    status: "complete",
    analysis: {
      summary:
        "Aktualisiertes Unternehmensrisikoregister mit 64 erfassten Risiken über 6 Kategorien. 9 als „hoch“ klassifiziert (5× operativ, 3× regulatorisch, 1× finanziell). Top-Risiko: Single-Source-Abhängigkeit beim Halbleiterlieferanten (Score 20). Gesamtrisikoexposition geschätzt auf 14,8 Mio. €.",
      entities: [
        { text: "64 Risiken", type: "Stichprobe", confidence: 0.99 },
        { text: "9 hohe Risiken", type: "Kennzahl", confidence: 0.98 },
        { text: "Score 20", type: "Score", confidence: 0.97 },
        { text: "14,8 Mio. €", type: "Umsatz", confidence: 0.96 },
        { text: "Halbleiterlieferant", type: "Geschäftsbereich", confidence: 0.93 },
        { text: "6 Kategorien", type: "Architektur", confidence: 0.95 },
      ],
      keyStats: [
        { label: "Risiken", value: "64" },
        { label: "Hoch", value: "9" },
        { label: "Mittel", value: "23" },
        { label: "Gering", value: "32" },
        { label: "Exposition", value: "14,8 Mio. €" },
        { label: "Top-Score", value: "20" },
      ],
      recommendations: [
        "Zweitlieferantenstrategie für kritische Halbleiterkomponenten innerhalb von 9 Monaten aufbauen",
        "Quartalsweise Risiko-Reviews mit dem Vorstand statt nur jährlich",
        "Frühindikatoren für die 9 hohen Risiken definieren und automatisch tracken",
      ],
    },
  },
  {
    id: "fb3",
    name: "Strategie-Workshop-Notizen.docx",
    size: "380 KB",
    type: "docx",
    pages: 12,
    status: "complete",
    analysis: {
      summary:
        "Protokoll des zweitägigen Strategie-Workshops mit 18 Führungskräften (März 2026). Kernergebnis: Verschiebung des Investitionsfokus von Bestandsmärkten DACH auf Eintritt in nordische Märkte (DK, SE, NO) bis 2027. Drei Wachstumsoptionen bewertet, Option B (Akquisition lokaler Distributor) bevorzugt.",
      entities: [
        { text: "18 Führungskräfte", type: "Stichprobe", confidence: 0.97 },
        { text: "DACH", type: "Geschäftsbereich", confidence: 0.98 },
        { text: "Nordische Märkte", type: "Geschäftsbereich", confidence: 0.96 },
        { text: "2027", type: "Zeitraum", confidence: 0.99 },
        { text: "Option B", type: "Architektur", confidence: 0.92 },
        { text: "3 Wachstumsoptionen", type: "Kennzahl", confidence: 0.94 },
      ],
      keyStats: [
        { label: "Teilnehmer", value: "18" },
        { label: "Optionen", value: "3" },
        { label: "Zielmärkte", value: "DK/SE/NO" },
        { label: "Horizont", value: "2027" },
        { label: "Empfehlung", value: "Option B" },
        { label: "Seiten", value: "12" },
      ],
      recommendations: [
        "Due-Diligence-Prozess für 2–3 Distributor-Kandidaten in DK/SE bis Q3 2026 starten",
        "Marktanalyse Norwegen vertiefen — derzeit dünnste Datenbasis der drei Länder",
        "Steering Committee für Internationalisierung mit monatlicher Kadenz einrichten",
      ],
    },
  },
  {
    id: "fb4",
    name: "Org-Chart-2026.png",
    size: "640 KB",
    type: "image",
    pages: 1,
    status: "complete",
    analysis: {
      summary:
        "Aktuelles Organigramm Stand März 2026: 4 Geschäftsbereiche, 18 Abteilungen, 247 Mitarbeitende. Identifiziert eine ungewöhnlich flache Struktur in der Tech-Organisation (Spannweite 1:14) und drei Vakanzen auf Direktorenebene.",
      entities: [
        { text: "247 Mitarbeitende", type: "Stichprobe", confidence: 0.99 },
        { text: "18 Abteilungen", type: "Architektur", confidence: 0.98 },
        { text: "4 Geschäftsbereiche", type: "Geschäftsbereich", confidence: 0.99 },
        { text: "Spannweite 1:14", type: "Kennzahl", confidence: 0.95 },
        { text: "3 Vakanzen", type: "Kennzahl", confidence: 0.96 },
      ],
      keyStats: [
        { label: "FTE", value: "247" },
        { label: "Bereiche", value: "4" },
        { label: "Abteilungen", value: "18" },
        { label: "Vakanzen", value: "3" },
        { label: "Max. Spannweite", value: "1:14" },
        { label: "Stand", value: "03/26" },
      ],
      recommendations: [
        "Tech-Organisation um eine Lead-Engineer-Ebene erweitern, um Spannweite auf 1:8 zu senken",
        "Direktorenvakanzen priorisieren — derzeit doppelte Berichtslinien an CEO",
        "Quartalsweise Aktualisierung des Organigramms etablieren statt jährlich",
      ],
    },
  },
];

// ---- File-type filtering -------------------------------------------------

export const ALLOWED_EXTS = [
  "pdf",
  "doc", "docx", "docm", "dot", "dotx", "dotm", "rtf", "odt",
  "xls", "xlsx", "xlsm", "xlsb", "xlt", "xltx", "xltm", "ods",
  "ppt", "pptx", "pptm", "pps", "ppsx", "ppsm", "pot", "potx", "potm", "odp",
  "csv", "tsv", "txt", "md", "log", "json",
  "png", "jpg", "jpeg", "gif", "svg", "webp", "bmp", "tif", "tiff", "heic", "heif", "avif", "ico",
];

export const ACCEPT_ATTR =
  ".pdf,.doc,.docx,.docm,.dot,.dotx,.dotm,.rtf,.odt,.xls,.xlsx,.xlsm,.xlsb,.xlt,.xltx,.xltm,.ods,.ppt,.pptx,.pptm,.pps,.ppsx,.ppsm,.pot,.potx,.potm,.odp,.csv,.tsv,.txt,.md,.log,.json,image/*";

export function isAllowedFile(file: File): boolean {
  const name = (file && file.name) || "";
  const ext = name.split(".").pop()?.toLowerCase() || "";
  return ALLOWED_EXTS.includes(ext);
}

export function filterAllowedFiles(fileList: FileList | File[] | null | undefined) {
  const arr = Array.from(fileList || []);
  const accepted: File[] = [];
  const rejected: File[] = [];
  for (const f of arr) (isAllowedFile(f) ? accepted : rejected).push(f);
  return { accepted, rejected };
}

export function inferFileType(name: string): FileItem["type"] {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  if (ext === "pdf") return "pdf";
  if (["doc", "docx"].includes(ext)) return "docx";
  if (["csv", "xlsx", "xls"].includes(ext)) return "csv";
  if (["png", "jpg", "jpeg", "gif", "svg", "webp"].includes(ext)) return "image";
  return "pdf";
}

export function mockAnalysis(fileName: string): FileAnalysis {
  const stem = fileName.replace(/\.[^.]+$/, "");
  return {
    summary:
      "Das Dokument „" +
      stem +
      "“ wurde erfolgreich indiziert. " +
      "Inhalte stehen jetzt für semantische Suche und Quellenangaben in diesem Projekt zur Verfügung.",
    keyStats: [
      { label: "Indizierte Abschnitte", value: String(8 + Math.floor(Math.random() * 24)) },
      { label: "Erkannte Tabellen", value: String(Math.floor(Math.random() * 4)) },
      { label: "Sprache", value: "Deutsch" },
    ],
    entities: [
      { text: "Geschäftsbereich", type: "Geschäftsbereich", confidence: 0.94 },
      { text: "Wachstum", type: "Wachstum", confidence: 0.88 },
      { text: "Kennzahl", type: "Kennzahl", confidence: 0.82 },
    ],
  };
}
