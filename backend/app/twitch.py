"""Twitch Helix client: app token, user lookup and exhaustive clip listing.

The /clips endpoint silently stops paginating after roughly 1,000 results for a
single date range, so we walk time in windows and split any window that looks
truncated until every piece is small enough to list completely.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterator

import httpx

from . import config

HELIX = "https://api.twitch.tv/helix"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
# Clips launched on Twitch in May 2016.
CLIPS_EPOCH = datetime(2016, 5, 1, tzinfo=timezone.utc)
SPLIT_THRESHOLD = 900
MIN_WINDOW = timedelta(hours=1)


class TwitchError(RuntimeError):
    pass


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class TwitchClient:
    def __init__(self, client_id: str | None = None, client_secret: str | None = None,
                 http: httpx.Client | None = None):
        s = config.get_settings()
        self.client_id = client_id or s.twitch_client_id
        self.client_secret = client_secret or s.twitch_client_secret
        if not self.client_id or not self.client_secret:
            raise TwitchError("Add your Twitch Client ID and Secret in Settings first.")
        self.http = http or httpx.Client(timeout=30)
        self._token: str | None = None
        self._token_expiry = 0.0

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        r = self.http.post(TOKEN_URL, params={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials",
        })
        if r.status_code != 200:
            raise TwitchError(f"Twitch rejected the Client ID/Secret ({r.status_code}). Check Settings.")
        data = r.json()
        self._token = data["access_token"]
        self._token_expiry = time.time() + data.get("expires_in", 3600)
        return self._token

    def get(self, path: str, params: dict | list) -> dict:
        for attempt in range(5):
            r = self.http.get(f"{HELIX}{path}", params=params, headers={
                "Client-Id": self.client_id,
                "Authorization": f"Bearer {self._get_token()}",
            })
            if r.status_code == 401:
                self._token = None
                continue
            if r.status_code == 429:
                reset = float(r.headers.get("Ratelimit-Reset", time.time() + 2))
                time.sleep(max(1.0, min(30.0, reset - time.time())))
                continue
            if r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            if r.status_code != 200:
                raise TwitchError(f"Twitch API {path} failed: {r.status_code} {r.text[:200]}")
            return r.json()
        raise TwitchError(f"Twitch API {path} kept failing, try again later.")

    def get_user(self, login: str) -> dict:
        login = login.strip().lower().lstrip("@")
        if "twitch.tv/" in login:
            login = login.split("twitch.tv/")[1].split("/")[0]
        data = self.get("/users", {"login": login}).get("data", [])
        if not data:
            raise TwitchError(f"No Twitch user called '{login}'.")
        return data[0]

    def get_games(self, ids: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        ids = [i for i in dict.fromkeys(ids) if i]
        for i in range(0, len(ids), 100):
            chunk = ids[i:i + 100]
            data = self.get("/games", [("id", g) for g in chunk]).get("data", [])
            out.update({g["id"]: g["name"] for g in data})
        return out

    def _list_window(self, broadcaster_id: str, start: datetime, end: datetime) -> list[dict]:
        clips: list[dict] = []
        cursor = None
        while True:
            params = {"broadcaster_id": broadcaster_id, "started_at": iso(start),
                      "ended_at": iso(end), "first": 100}
            if cursor:
                params["after"] = cursor
            page = self.get("/clips", params)
            clips.extend(page.get("data", []))
            cursor = page.get("pagination", {}).get("cursor")
            if not cursor or not page.get("data"):
                return clips

    def list_clips(self, broadcaster_id: str, start: datetime, end: datetime) -> list[dict]:
        """All clips in [start, end), splitting windows that hit Twitch's cap."""
        clips = self._list_window(broadcaster_id, start, end)
        if len(clips) < SPLIT_THRESHOLD or end - start <= MIN_WINDOW:
            return clips
        mid = start + (end - start) / 2
        seen: dict[str, dict] = {}
        for part in (self.list_clips(broadcaster_id, start, mid),
                     self.list_clips(broadcaster_id, mid, end)):
            for c in part:
                seen[c["id"]] = c
        return list(seen.values())

    def sweep(self, broadcaster_id: str, start: datetime, end: datetime,
              step: timedelta = timedelta(days=7),
              progress: Callable[[float, str], None] | None = None) -> Iterator[list[dict]]:
        """Yield clip batches window by window, newest first."""
        total = (end - start).total_seconds() or 1
        cur_end = end
        while cur_end > start:
            cur_start = max(start, cur_end - step)
            batch = self.list_clips(broadcaster_id, cur_start, cur_end)
            if progress:
                done = (end - cur_start).total_seconds() / total
                progress(done, f"Scanned back to {cur_start:%d %b %Y}")
            yield batch
            cur_end = cur_start
