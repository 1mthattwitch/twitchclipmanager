"""Measure how often the AI gets clips wrong, on your own clips and hardware.

    python -m app.evaluate report                 # what the self-checks and your corrections say
    python -m app.evaluate agreement --n 40       # re-describe clips with another AI and compare
    python -m app.evaluate spotcheck --n 25       # you judge a random sample (the real error rate)
    python -m app.evaluate search                 # search accuracy with the real meaning-search model

Run from the backend folder with the app's virtualenv active. Use "report" after each change
(model, frames per clip, Claude cross-check) to see whether the error rate went down.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import tempfile
import time
from pathlib import Path

from . import config, db


def wilson(errors: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% confidence interval for an error rate from a small sample."""
    if n == 0:
        return 0.0, 1.0
    p = errors / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


# ---------- report ----------

def report() -> dict:
    analysed = db.query_one("SELECT COUNT(*) n FROM clips WHERE status='done'")["n"]
    if not analysed:
        print("No analysed clips yet.")
        return {}
    review = db.query_one("SELECT COUNT(*) n FROM clips WHERE status='done' AND needs_review=1")["n"]
    corrected = db.query_one("SELECT COUNT(*) n FROM clips WHERE corrected=1")["n"]
    checks = db.query("SELECT supported FROM qa WHERE kind IN ('verify','crosscheck')")
    refuted = sum(1 for c in checks if c["supported"] == 0)
    unsure = sum(1 for c in checks if c["supported"] is None)
    failed = db.query_one("SELECT COUNT(*) n FROM clips WHERE status='error'")["n"]
    labels = _load_labels()
    out = {
        "analysed": analysed,
        "needs_review_rate": review / analysed,
        "corrected_rate": corrected / analysed,
        "claims_checked": len(checks),
        "claims_refuted_rate": refuted / len(checks) if checks else None,
        "claims_unsure_rate": unsure / len(checks) if checks else None,
        "pipeline_failures": failed,
        "spotcheck": _label_stats(labels),
    }
    print(f"Analysed clips:            {analysed}")
    print(f"Pipeline failures:         {failed}")
    print(f"Flagged 'needs a look':    {pct(out['needs_review_rate'])}")
    print(f"Corrected by you:          {pct(out['corrected_rate'])}")
    if checks:
        print(f"Self-check claims:         {len(checks)} checked, {pct(out['claims_refuted_rate'])} refuted, "
              f"{pct(out['claims_unsure_rate'])} unsure")
    s = out["spotcheck"]
    if s["n"]:
        lo, hi = s["ci"]
        print(f"Spot-check error rate:     {pct(s['error_rate'])} (95% CI {pct(lo)}–{pct(hi)}, {s['n']} clips judged by you)")
        print(f"  wrong category:          {pct(s['category_error_rate'])}")
        print(f"  wrong/misleading summary:{pct(s['summary_error_rate']):>7}")
    else:
        print("Spot-check error rate:     not measured yet — run: python -m app.evaluate spotcheck")
    _advice(out)
    return out


def _advice(out: dict) -> None:
    tips = []
    s = out.get("spotcheck") or {}
    rate = s.get("error_rate")
    if rate is not None and s.get("n"):
        if rate > 0.10:
            tips.append("Error rate is high: try Claude for analysis, or raise 'Frames per clip' to 10.")
        elif rate > 0.03:
            tips.append("Turn on 'Cross-check unsure local results' so Claude re-checks the shaky ones.")
        else:
            tips.append("Error rate is low. Keep spot-checking occasionally after changing models.")
    if (out.get("claims_refuted_rate") or 0) > 0.1:
        tips.append("Many self-check claims were refuted: the describing model is guessing; a larger model will help.")
    if out.get("pipeline_failures"):
        tips.append("Some clips failed: open the Queue tab and press 'Retry failed'.")
    for t in tips:
        print("→ " + t)


# ---------- agreement ----------

def agreement(n: int, against: str | None, seed: int = 0) -> dict:
    """Re-describe analysed clips with another provider (or the same one) and compare."""
    from .ai.base import get_provider
    from .analyze import describe, get_clip

    s = config.get_settings()
    other = against or ("claude" if s.ai_mode == "local" else "local")
    provider = get_provider(other)
    ok, why = provider.available()
    if not ok:
        sys.exit(f"{other} isn't available: {why}")
    ids = [r["id"] for r in db.query(
        "SELECT id FROM clips WHERE status='done' AND frames IS NOT NULL AND corrected=0")]
    random.Random(seed).shuffle(ids)
    ids = ids[:n]
    if not ids:
        sys.exit("No analysed clips to compare.")
    same_cat = 0
    jaccards = []
    rows = []
    for i, cid in enumerate(ids, 1):
        clip = get_clip(cid)
        try:
            second = describe(clip, provider)
        except Exception as e:  # keep going; report what failed
            print(f"  [{i}/{len(ids)}] {clip['title'][:40]!r}: {other} failed ({e})")
            continue
        a, b = set(clip.get("tags") or []), set(second["tags"])
        jac = len(a & b) / len(a | b) if a | b else 1.0
        match = clip["category"] == second["category"]
        same_cat += match
        jaccards.append(jac)
        rows.append({"clip": cid, "title": clip["title"], "first": clip["category"], "second": second["category"]})
        print(f"  [{i}/{len(ids)}] {'✓' if match else '✗'} {clip['title'][:40]!r}: {clip['category']} vs {second['category']}")
    done = len(jaccards)
    if not done:
        sys.exit("Nothing could be compared.")
    disagree = 1 - same_cat / done
    lo, hi = wilson(done - same_cat, done)
    print(f"\nCategory disagreement with {other}: {pct(disagree)} (95% CI {pct(lo)}–{pct(hi)}, n={done})")
    print(f"Average tag overlap: {pct(sum(jaccards) / done)}")
    print("Disagreement is an upper bound on how often *one* of them is wrong. Open the ✗ clips to see who was right.")
    return {"n": done, "disagreement": disagree, "tag_overlap": sum(jaccards) / done, "rows": rows}


# ---------- human spot-check ----------

def _labels_path() -> Path:
    return config.DATA_DIR / "spotcheck.jsonl"


def _load_labels() -> list[dict]:
    p = _labels_path()
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text("utf-8").splitlines() if line.strip()]


def _label_stats(labels: list[dict]) -> dict:
    n = len(labels)
    if not n:
        return {"n": 0, "error_rate": None, "ci": (0, 1), "category_error_rate": None, "summary_error_rate": None}
    wrong = sum(1 for l in labels if not (l["category_ok"] and l["summary_ok"]))
    return {
        "n": n,
        "error_rate": wrong / n,
        "ci": wilson(wrong, n),
        "category_error_rate": sum(1 for l in labels if not l["category_ok"]) / n,
        "summary_error_rate": sum(1 for l in labels if not l["summary_ok"]) / n,
    }


def spotcheck(n: int) -> None:
    import webbrowser
    done = {l["clip"] for l in _load_labels()}
    ids = [r["id"] for r in db.query("SELECT id FROM clips WHERE status='done' AND corrected=0")
           if r["id"] not in done]
    random.shuffle(ids)
    ids = ids[:n]
    if not ids:
        print("Nothing new to check.")
        return
    print("For each clip, watch it (it opens in your browser), then answer. Ctrl+C to stop any time.\n")
    with _labels_path().open("a", encoding="utf-8") as f:
        for i, cid in enumerate(ids, 1):
            c = db.query_one("SELECT * FROM clips WHERE id=?", [cid])
            print(f"[{i}/{len(ids)}] {c['title']}")
            print(f"   AI category: {c['category']}")
            print(f"   AI summary:  {c['summary']}")
            webbrowser.open(c["url"])
            cat_ok = input("   Category right? [y/n] ").strip().lower().startswith("y")
            sum_ok = input("   Summary accurate (nothing important wrong)? [y/n] ").strip().lower().startswith("y")
            f.write(json.dumps({"clip": cid, "category_ok": cat_ok, "summary_ok": sum_ok,
                                "provider": c["provider"], "at": time.time()}) + "\n")
            f.flush()
            print()
    report()


# ---------- search ----------

def search_bench() -> dict:
    """Search accuracy on the built-in labelled set, in a throwaway database, with the real model."""
    from . import bench_data, embed, search
    from .analyze import reembed

    real_dir = config.DATA_DIR
    tmp = Path(tempfile.mkdtemp())
    config.DATA_DIR = tmp
    db.reset_for_tests()
    try:
        sid = db.execute("INSERT INTO streamers (twitch_id, login, display_name, created_at) VALUES ('b','bench','Bench',0)").lastrowid

        def add_streamer(_login):
            return sid

        def add_clip(streamer_id, cid, title, **fields):
            db.execute("INSERT INTO clips (id, streamer_id, url, title, created_at, duration) VALUES (?,?,?,?,?,?)",
                       [cid, streamer_id, "u", title, "2025-01-01", 30])
            db.update("clips", "id", cid, **fields)
            db.reindex_clip(cid)

        bench_data.seed(add_streamer, add_clip)
        has_model = embed.available()
        if has_model:
            for cid, *_ in bench_data.CLIPS:
                reembed(cid)
        results = {}
        for name, qs in (("normal", bench_data.QUERIES), ("synonyms", bench_data.HARD_QUERIES),
                         ("held-out", bench_data.HELDOUT_QUERIES)):
            h1 = h5 = 0
            for q, expected in qs:
                ids = [r["id"] for r in search.search(q, limit=5)["results"]]
                h1 += ids[:1] == [expected]
                h5 += expected in ids
            results[name] = (h1 / len(qs), h5 / len(qs))
            print(f"{name:>9}: top-1 {pct(h1 / len(qs))}, top-5 {pct(h5 / len(qs))}  ({len(qs)} queries)")
        print("mode:", "keyword + meaning (hybrid)" if has_model else "keyword only (pip install fastembed for meaning search)")
        return results
    finally:
        config.DATA_DIR = real_dir
        db.reset_for_tests()
        search.invalidate_cache()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m app.evaluate", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("report")
    a = sub.add_parser("agreement")
    a.add_argument("--n", type=int, default=40)
    a.add_argument("--against", choices=["local", "claude"], default=None)
    s = sub.add_parser("spotcheck")
    s.add_argument("--n", type=int, default=25)
    sub.add_parser("search")
    args = ap.parse_args(argv)
    if args.cmd == "report":
        report()
    elif args.cmd == "agreement":
        agreement(args.n, args.against)
    elif args.cmd == "spotcheck":
        spotcheck(args.n)
    else:
        search_bench()


if __name__ == "__main__":
    main()
