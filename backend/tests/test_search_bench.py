"""Search quality benchmark. Run with -s to see the report."""
from app import search
from app.bench_data import QUERIES, seed
from conftest import add_clip, add_streamer


def run_bench():
    seed(add_streamer, add_clip)
    top1 = top5 = 0
    misses = []
    for q, expected in QUERIES:
        ids = [r["id"] for r in search.search(q, limit=5)["results"]]
        if ids[:1] == [expected]:
            top1 += 1
        if expected in ids:
            top5 += 1
        else:
            misses.append((q, expected, ids[:3]))
    return top1 / len(QUERIES), top5 / len(QUERIES), misses


def test_keyword_search_benchmark():
    r1, r5, misses = run_bench()
    print(f"\nkeyword search: top-1 {r1:.0%}, top-5 {r5:.0%} on {len(QUERIES)} queries")
    for m in misses:
        print("  miss:", m)
    assert r5 >= 0.95, misses
    assert r1 >= 0.85


def test_hard_queries_report():
    """Not a pass/fail gate for keyword mode: it shows what meaning search must cover."""
    from app.bench_data import HARD_QUERIES
    seed(add_streamer, add_clip)
    hits1 = hits5 = 0
    for q, expected in HARD_QUERIES:
        ids = [r["id"] for r in search.search(q, limit=5)["results"]]
        hits1 += ids[:1] == [expected]
        hits5 += expected in ids
        if expected not in ids:
            print("  hard miss:", q, "->", ids[:3])
    print(f"\nkeyword search, hard queries: top-1 {hits1 / len(HARD_QUERIES):.0%}, top-5 {hits5 / len(HARD_QUERIES):.0%}")


def test_heldout_queries():
    from app.bench_data import HELDOUT_QUERIES
    seed(add_streamer, add_clip)
    hits1 = hits5 = 0
    for q, expected in HELDOUT_QUERIES:
        ids = [r["id"] for r in search.search(q, limit=5)["results"]]
        hits1 += ids[:1] == [expected]
        hits5 += expected in ids
        if ids[:1] != [expected]:
            print("  held-out miss:", q, expected, "->", ids[:3])
    r1, r5 = hits1 / len(HELDOUT_QUERIES), hits5 / len(HELDOUT_QUERIES)
    print(f"\nkeyword search, held-out: top-1 {r1:.0%}, top-5 {r5:.0%}")
    assert r5 >= 0.9
