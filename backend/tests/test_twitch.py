from datetime import datetime, timedelta, timezone

import httpx

from app.twitch import TwitchClient, parse_iso

START = datetime(2025, 1, 1, tzinfo=timezone.utc)


def fake_twitch(clips, cap=1000, calls=None):
    """A mock Helix API that, like the real one, stops paginating after `cap` results."""
    def handler(request: httpx.Request):
        if request.url.host == "id.twitch.tv":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if calls is not None:
            calls.append(request.url.path)
        if request.url.path.endswith("/games"):
            ids = request.url.params.get_list("id")
            return httpx.Response(200, json={"data": [{"id": i, "name": f"Game {i}"} for i in ids]})
        p = request.url.params
        lo, hi = parse_iso(p["started_at"]), parse_iso(p["ended_at"])
        matching = [c for c in clips if lo <= parse_iso(c["created_at"]) < hi][:cap]
        offset = int(p.get("after") or 0)
        page = matching[offset:offset + 100]
        nxt = offset + 100
        cursor = str(nxt) if nxt < len(matching) else None
        return httpx.Response(200, json={"data": page, "pagination": {"cursor": cursor} if cursor else {}})
    return httpx.Client(transport=httpx.MockTransport(handler))


def make_clips(n, span=timedelta(days=7)):
    return [{"id": f"clip{i}", "created_at": (START + span * i / n).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "title": f"t{i}", "game_id": str(i % 3)} for i in range(n)]


def test_list_clips_splits_windows_past_the_cap():
    clips = make_clips(2500)
    tc = TwitchClient("id", "secret", http=fake_twitch(clips))
    got = tc.list_clips("b", START, START + timedelta(days=7))
    assert len(got) == 2500
    assert len({c["id"] for c in got}) == 2500


def test_sweep_covers_whole_range_and_reports_progress():
    clips = make_clips(300, span=timedelta(days=30))
    tc = TwitchClient("id", "secret", http=fake_twitch(clips))
    seen, progress = [], []
    for batch in tc.sweep("b", START, START + timedelta(days=30), progress=lambda p, m: progress.append(p)):
        seen.extend(batch)
    assert len({c["id"] for c in seen}) == 300
    assert progress[-1] == 1.0


def test_games_lookup():
    tc = TwitchClient("id", "secret", http=fake_twitch([]))
    assert tc.get_games(["1", "2", "1", ""]) == {"1": "Game 1", "2": "Game 2"}
