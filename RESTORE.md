# sleek-rag — backup & restore guide

How to make a **complete, machine-portable backup** of this project and restore
it on a new machine. The backup includes live secrets, so treat the archive
like a password: keep it encrypted, delete plaintext copies after transfer.

## What a full backup must contain

Two things live **outside** what a plain `git clone` gives you:

1. `.env` — real secrets, git-ignored (never on the remote).
2. The GCP service-account key — lives outside the repo at
   `~/.config/sleek-rag/sleek-rag-b80deaaff5c7.json` (path is set by
   `GCP_SERVICE_ACCOUNT_JSON_PATH` in `.env`).

A backup that omits either is incomplete.

## Create the backup

Excludes the regenerable bulk (~2.3 GB) and bundles the repo (with `.git`
history), the external GCP key, and this guide:

```bash
STAMP=$(date +%F)
mkdir -p ~/sleek-rag-backup-stage/secrets-external
cp ~/.config/sleek-rag/sleek-rag-b80deaaff5c7.json ~/sleek-rag-backup-stage/secrets-external/
cp ~/sleek-rag/RESTORE.md ~/sleek-rag-backup-stage/

tar czf ~/sleek-rag-backup-$STAMP.tar.gz \
  --exclude=node_modules --exclude=venv --exclude=.next \
  --exclude=__pycache__ --exclude=.pytest_cache \
  --exclude='scripts/benchmark/output' \
  --exclude=tsconfig.tsbuildinfo --exclude=next-env.d.ts \
  -C ~ sleek-rag \
  -C ~/sleek-rag-backup-stage secrets-external RESTORE.md
```

**Encrypt it** (never move the plaintext around) — prompts for a passphrase:

```bash
gpg -c --cipher-algo AES256 -o ~/sleek-rag-backup-$STAMP.tar.gz.gpg ~/sleek-rag-backup-$STAMP.tar.gz \
  && gpg -d ~/sleek-rag-backup-$STAMP.tar.gz.gpg | tar tz >/dev/null && echo VERIFIED-OK
```

Then wipe the plaintext + staging:

```bash
shred -u ~/sleek-rag-backup-$STAMP.tar.gz && rm -rf ~/sleek-rag-backup-stage
```

**Excluded** (regenerable): `node_modules/`, `backend/venv/`,
`scripts/benchmark/venv/`, `.next/`, `__pycache__/`, `.pytest_cache/`,
`scripts/benchmark/output/`, `tsconfig.tsbuildinfo`, `next-env.d.ts`.

Git remote (private): `git@github.com:lukas-sleek/sleek-rag.git`.

## Restore on the new machine

1. **Decrypt & extract**
   ```bash
   gpg -d ~/sleek-rag-backup-<date>.tar.gz.gpg | tar xz -C ~/
   cd ~/sleek-rag
   ```

2. **Place the GCP key** where `.env`'s `GCP_SERVICE_ACCOUNT_JSON_PATH` points
   (original: `~/.config/sleek-rag/`); update the var if the new path differs:
   ```bash
   mkdir -p ~/.config/sleek-rag
   cp ../secrets-external/sleek-rag-b80deaaff5c7.json ~/.config/sleek-rag/
   ```

3. **Frontend deps:** `npm install`

4. **Backend venv** (Python 3.10, venv required per CLAUDE.md):
   ```bash
   cd backend && python3 -m venv venv && source venv/bin/activate \
     && pip install -r requirements.txt && cd ..
   ```

5. **Host prerequisites** not captured by the backup — install separately:
   - headless **LibreOffice** (Office→PDF conversion)
   - Google Cloud SDK / `gcloud` (if used beyond the SA key)

6. **Run:** `npm run dev` (frontend) + start the FastAPI backend as before.

## Secret inventory (key names; values live in `.env` / the SA JSON)

- **Supabase:** `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`,
  `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`
- **LangSmith:** `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, `LANGSMITH_ENDPOINT`
- **GCP:** `GCP_PROJECT_ID`, `GCP_SERVICE_ACCOUNT_JSON_PATH`,
  `GCS_STAGING_BUCKET`, `GCS_FILES_BUCKET`, `GCP_LOCATION`,
  `DOCUMENTAI_US_LOCATION`, `DOCUMENTAI_US_PROCESSOR_ID`,
  `VERTEX_RAG_EMBEDDING_MODEL`
- **Gemini/LLM:** `GEMINI_API_KEY`, `GEMINI_BASE_URL`, `GEMINI_CHAT_MODEL`,
  `GEMINI_EMBEDDING_MODEL`, `GEMINI_EMBEDDING_DIM`

> **Rotate after a migration.** Once the new machine is live, rotate the
> Supabase service-role key, Gemini/LangSmith API keys, and the GCP
> service-account key — a backup that has traveled is the right moment to rotate.
