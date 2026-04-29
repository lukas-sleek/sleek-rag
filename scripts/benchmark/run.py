"""Top-level benchmark orchestrator.

Usage:

  python -m scripts.benchmark.run --variants A,B,C --judge
  python -m scripts.benchmark.run --variants A,B
  python -m scripts.benchmark.run --questions custom.json
  python -m scripts.benchmark.run \
        --regenerate-report-from scripts/benchmark/output/20260429T101010-results.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.benchmark.env_loader import load_env  # noqa: E402

OUTPUT_DIR = ROOT / "scripts" / "benchmark" / "output"


def _run_variant_a(questions: list[dict]) -> list[dict]:
    from scripts.benchmark.clients.vanilla import run_question_set as run_vanilla

    return run_vanilla(questions)


async def _run_variant_b(questions: list[dict]) -> list[dict]:
    from scripts.benchmark.clients.current_chat import run_question_set as run_chat

    return await run_chat(questions)


async def _run_variant_c(questions: list[dict]) -> list[dict]:
    from scripts.benchmark.clients.current_v2 import run_question_set as run_v2

    return await run_v2(questions)


def _merge_per_question(
    questions: list[dict],
    variant_results: dict[str, list[dict]],
) -> list[dict]:
    by_label_by_id: dict[str, dict[str, dict]] = {
        label: {entry["id"]: entry for entry in results}
        for label, results in variant_results.items()
    }
    out: list[dict] = []
    for q in questions:
        variants: dict[str, dict] = {}
        for label, idx in by_label_by_id.items():
            entry = idx.get(q["id"])
            if entry is None:
                variants[label] = {"answer": "", "_error": "no result"}
            else:
                variants[label] = {
                    k: v for k, v in entry.items() if k not in ("id", "question")
                }
        out.append(
            {
                "id": q["id"],
                "question": q["question"],
                "variants": variants,
            }
        )
    return out


def _add_judge_scores(per_question: list[dict], source_dir: str | None) -> None:
    from scripts.benchmark.judge import judge_results

    judged = judge_results(per_question, source_dir=source_dir)
    by_id = {e["id"]: e["judgments"] for e in judged}
    for entry in per_question:
        if entry["id"] in by_id:
            entry["judgments"] = by_id[entry["id"]]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variants", default="A,B,C")
    parser.add_argument("--judge", action="store_true")
    parser.add_argument(
        "--questions", default=str(Path(__file__).parent / "questions.json")
    )
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument(
        "--regenerate-report-from",
        default=None,
        help="Path to a prior {ts}-results.json — re-render report without re-querying.",
    )
    parser.add_argument(
        "--judge-source-dir",
        default=os.environ.get("BENCHMARK_JUDGE_SOURCE_DIR"),
        help="Directory of source PDFs to upload as judge ground-truth.",
    )
    args = parser.parse_args()

    load_env()

    from scripts.benchmark.report import generate_report

    if args.regenerate_report_from:
        cached = json.loads(Path(args.regenerate_report_from).read_text())
        per_question = cached["per_question"]
        variant_labels = cached["variant_labels"]
        out_path = generate_report(
            per_question=per_question,
            variant_labels=variant_labels,
            output_dir=args.output_dir,
        )
        print(f"report regenerated: {out_path}")
        return 0

    questions = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    variant_labels = [v.strip().upper() for v in args.variants.split(",") if v.strip()]
    runners = {"A": _run_variant_a}

    async def run_async(label: str) -> list[dict]:
        if label == "A":
            return _run_variant_a(questions)
        if label == "B":
            return await _run_variant_b(questions)
        if label == "C":
            return await _run_variant_c(questions)
        raise ValueError(f"unknown variant: {label}")

    async def run_all() -> dict[str, list[dict]]:
        results: dict[str, list[dict]] = {}
        for label in variant_labels:
            print(f"==> Variant {label} starting", flush=True)
            try:
                results[label] = await run_async(label)
                print(
                    f"==> Variant {label} done ({len(results[label])} answers)",
                    flush=True,
                )
            except Exception as exc:
                traceback.print_exc()
                print(f"==> Variant {label} FAILED: {exc}", flush=True)
                results[label] = []
        return results

    raw = asyncio.run(run_all())
    per_question = _merge_per_question(questions, raw)

    if args.judge:
        try:
            print("==> Judge starting", flush=True)
            _add_judge_scores(per_question, args.judge_source_dir)
            print("==> Judge done", flush=True)
        except Exception:
            traceback.print_exc()
            print("==> Judge FAILED — continuing without scores", flush=True)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    results_path = output_dir / f"{ts}-results.json"
    results_path.write_text(
        json.dumps(
            {"variant_labels": variant_labels, "per_question": per_question},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    report_path = generate_report(
        per_question=per_question,
        variant_labels=variant_labels,
        output_dir=output_dir,
        timestamp=ts,
    )
    print(f"results: {results_path}")
    print(f"report:  {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
