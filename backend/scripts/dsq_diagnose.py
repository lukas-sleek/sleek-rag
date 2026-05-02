"""DSQ 429 diagnostic. Confirms that the 429s we see in chat are
DSQ shared-pool burst-throttles (concurrency-bound), not project-level
quota events.

Three experiments, run back-to-back, all using gcloud's user/SA token:

  E1  Burst-vs-spread:    same workload (30 calls) in (a) full burst
                          vs (b) spread over 30s. Compares 429 rates.
  E2  Region comparison:  identical 30-call burst against
                          global / europe-west1 / europe-west3.
  E3  Quota-meter check:  pulls Cloud Monitoring 'quota/exceeded'
                          counters for the relevant Vertex meters
                          AFTER the bursts. If our claim is right
                          they stay at 0 even when we provoke 429.

Run:
    python backend/scripts/dsq_diagnose.py

Prereqs:
    gcloud auth login
    gcloud config set project 1007445049099

Output goes to stdout + a JSON file at /tmp/dsq_diagnose_<ts>.json.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Config (override via env)
# ---------------------------------------------------------------------------

PROJECT = os.environ.get("DSQ_PROJECT", "1007445049099")
MODEL = os.environ.get("DSQ_MODEL", "gemini-2.5-flash")
REGIONS_BURST = ["global", "europe-west1", "europe-west3"]
N_BURST = int(os.environ.get("DSQ_N", "30"))            # parallelism
SPREAD_SECONDS = int(os.environ.get("DSQ_SPREAD", "30"))  # spread duration

# Realistic prompt size (~2.4K tokens) — mirrors our orchestrator system
# prompt + history footprint when a chat sub-question fans out.
PROMPT_FILLER = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do "
    "eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut "
    "enim ad minim veniam, quis nostrud exercitation ullamco laboris "
    "nisi ut aliquip ex ea commodo consequat. "
) * 40
PROMPT = (
    "Du bist ein Hauptagent fuer Schweizer Bahn-/Ingenieurprojekt-"
    "Ausschreibungen. " + PROMPT_FILLER + " Frage: Wer ist der Projektleiter?"
)
MAX_OUTPUT_TOKENS = 800
HTTP_TIMEOUT = 60


def get_token() -> str:
    out = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        check=True, capture_output=True, text=True,
    )
    return out.stdout.strip()


def endpoint(region: str) -> str:
    if region == "global":
        host = "aiplatform.googleapis.com"
    else:
        host = f"{region}-aiplatform.googleapis.com"
    return (
        f"https://{host}/v1/projects/{PROJECT}/locations/{region}"
        f"/publishers/google/models/{MODEL}:generateContent"
    )


# ---------------------------------------------------------------------------
# Per-call worker
# ---------------------------------------------------------------------------


@dataclass
class CallResult:
    region: str
    status: int
    elapsed_ms: int
    error_status: str | None = None  # body.error.status when 4xx/5xx
    has_quota_failure: bool = False  # body.error.details[*].@type==QuotaFailure


async def one_call(client: httpx.AsyncClient, token: str, region: str) -> CallResult:
    body = {
        "contents": [{"role": "user", "parts": [{"text": PROMPT}]}],
        "generationConfig": {"maxOutputTokens": MAX_OUTPUT_TOKENS},
    }
    t0 = time.perf_counter()
    try:
        r = await client.post(
            endpoint(region),
            json=body,
            headers={"Authorization": f"Bearer {token}"},
            timeout=HTTP_TIMEOUT,
        )
        elapsed = int((time.perf_counter() - t0) * 1000)
        err_status: str | None = None
        has_qf = False
        if r.status_code != 200:
            try:
                doc = r.json().get("error", {})
                err_status = doc.get("status")
                for d in doc.get("details", []) or []:
                    if "QuotaFailure" in (d.get("@type") or ""):
                        has_qf = True
                        break
            except Exception:
                pass
        return CallResult(region, r.status_code, elapsed, err_status, has_qf)
    except Exception as exc:
        elapsed = int((time.perf_counter() - t0) * 1000)
        return CallResult(region, -1, elapsed, type(exc).__name__, False)


# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------


@dataclass
class Summary:
    label: str
    region: str
    n: int
    counts: dict[str, int] = field(default_factory=dict)
    quota_failures_in_429: int = 0
    p50_ms: int = 0
    p95_ms: int = 0


def summarise(label: str, region: str, results: list[CallResult]) -> Summary:
    counts: dict[str, int] = {}
    qf = 0
    lat200 = []
    for r in results:
        key = str(r.status)
        counts[key] = counts.get(key, 0) + 1
        if r.status == 429 and r.has_quota_failure:
            qf += 1
        if r.status == 200:
            lat200.append(r.elapsed_ms)
    p50 = int(statistics.median(lat200)) if lat200 else 0
    p95 = int(sorted(lat200)[int(0.95 * len(lat200))]) if len(lat200) >= 20 else (
        max(lat200) if lat200 else 0
    )
    return Summary(label, region, len(results), counts, qf, p50, p95)


async def burst(client: httpx.AsyncClient, token: str, region: str, n: int) -> list[CallResult]:
    """Fire n calls as concurrently as possible."""
    return await asyncio.gather(*[one_call(client, token, region) for _ in range(n)])


async def spread(client: httpx.AsyncClient, token: str, region: str, n: int, total_seconds: float) -> list[CallResult]:
    """Fire n calls evenly over total_seconds — same total work, no burst."""
    interval = total_seconds / max(n, 1)

    async def staggered(i: int) -> CallResult:
        await asyncio.sleep(i * interval)
        return await one_call(client, token, region)

    return await asyncio.gather(*[staggered(i) for i in range(n)])


# ---------------------------------------------------------------------------
# Cloud Monitoring quota-meter check
# ---------------------------------------------------------------------------


def quota_exceeded_24h(token: str) -> dict[str, int]:
    """Sum the 'quota/exceeded' counter for the four relevant Vertex meters
    over the last 24h. If our claim holds, all should be 0 even after
    bursts produced 429s."""
    metrics = [
        "global_generate_content_requests_per_minute_per_project_per_base_model",
        "global_generate_content_input_tokens_per_minute_per_base_model",
        "global_generate_content_output_tokens_per_minute_per_base_model",
        "generate_content_requests_per_minute_per_project_per_base_model",
    ]
    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(hours=24)
    fmt = lambda x: x.strftime("%Y-%m-%dT%H:%M:%SZ")
    out: dict[str, int] = {}
    with httpx.Client(timeout=30) as client:
        for m in metrics:
            r = client.get(
                f"https://monitoring.googleapis.com/v3/projects/{PROJECT}/timeSeries",
                params={
                    "filter": f'metric.type="aiplatform.googleapis.com/quota/{m}/exceeded"',
                    "interval.startTime": fmt(start),
                    "interval.endTime": fmt(end),
                    "aggregation.alignmentPeriod": "86400s",
                    "aggregation.perSeriesAligner": "ALIGN_SUM",
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            total = 0
            for s in (r.json().get("timeSeries") or []):
                for p in s.get("points", []) or []:
                    v = p.get("value", {})
                    total += int(v.get("int64Value") or float(v.get("doubleValue") or 0))
            out[m] = total
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def fmt_summary(s: Summary) -> str:
    parts = [f"{k}={v}" for k, v in sorted(s.counts.items())]
    return (
        f"  {s.label:<32}  region={s.region:<14}  n={s.n}  "
        f"{'  '.join(parts):<30}  qf-in-429={s.quota_failures_in_429}  "
        f"p50={s.p50_ms}ms p95={s.p95_ms}ms"
    )


async def main() -> int:
    token = get_token()
    print(f"PROJECT={PROJECT}  MODEL={MODEL}  N_BURST={N_BURST}  prompt_chars={len(PROMPT)} (~{len(PROMPT)//4} tokens)")
    summaries: list[Summary] = []

    async with httpx.AsyncClient(http2=False) as client:
        # E1: burst vs spread on global (same workload, different timing)
        print("\n[E1] burst vs spread (region=global, same n, same prompt)")
        r_b = await burst(client, token, "global", N_BURST)
        s_b = summarise("burst", "global", r_b)
        print(fmt_summary(s_b)); summaries.append(s_b)

        r_s = await spread(client, token, "global", N_BURST, SPREAD_SECONDS)
        s_s = summarise(f"spread/{SPREAD_SECONDS}s", "global", r_s)
        print(fmt_summary(s_s)); summaries.append(s_s)

        # E2: region comparison
        print("\n[E2] region comparison (burst, n={}, ~ same wall-clock)".format(N_BURST))
        for region in REGIONS_BURST:
            r = await burst(client, token, region, N_BURST)
            s = summarise("region-burst", region, r)
            print(fmt_summary(s)); summaries.append(s)

    # E3: project-quota meters AFTER all the bursts above
    print("\n[E3] project-level quota/exceeded counters (last 24h)")
    print("     If 429s above were DSQ shared-pool throttles, these stay 0.")
    qx = quota_exceeded_24h(token)
    for k, v in qx.items():
        print(f"  {k:70s} exceeded_24h={v}")

    # Verdict
    print("\n=== verdict ===")
    g_burst = next(s for s in summaries if s.label == "burst" and s.region == "global")
    g_spread = next(s for s in summaries if s.label.startswith("spread") and s.region == "global")
    burst_429 = g_burst.counts.get("429", 0)
    spread_429 = g_spread.counts.get("429", 0)
    qx_total = sum(qx.values())
    qf_total = sum(s.quota_failures_in_429 for s in summaries)
    print(f"  burst/global 429s: {burst_429}/{N_BURST}  ({burst_429/N_BURST:.0%})")
    print(f"  spread/global 429s: {spread_429}/{N_BURST}  ({spread_429/N_BURST:.0%})")
    print(f"  project-quota exceeded events 24h (across 4 meters): {qx_total}")
    print(f"  responses tagged QuotaFailure (project-quota): {qf_total}")
    if qx_total == 0 and qf_total == 0 and burst_429 > 0:
        print("  -> CONFIRMED: 429s are DSQ shared-pool throttles, not project-quota cap.")
    elif qf_total > 0:
        print("  -> 429s look LIKE project-quota events. Reconsider claim.")
    else:
        print("  -> No 429s reproduced — burst was insufficient or DSQ pool currently roomy.")

    out = {
        "project": PROJECT,
        "model": MODEL,
        "n_burst": N_BURST,
        "spread_seconds": SPREAD_SECONDS,
        "summaries": [asdict(s) for s in summaries],
        "quota_exceeded_24h": qx,
    }
    path = Path(f"/tmp/dsq_diagnose_{int(time.time())}.json")
    path.write_text(json.dumps(out, indent=2))
    print(f"\nfull results saved to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
