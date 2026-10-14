"""A small fake Riot API (httpx MockTransport handler) for crawler tests.

The world: per platform and tier a list of players, each with a match history (newest
first). Matches are copies of the real fixture match with ids/participants replaced.
"""

from __future__ import annotations

import copy
import json
import re
import zlib
from collections import Counter

import httpx

from riot_client.routing import APEX_TIERS, DIVISIONS, TIERS
from tests.conftest import FIXTURES_DIR

PAGE_SIZE = 3
_MATCH_TEMPLATE = json.loads((FIXTURES_DIR / "match.json").read_text(encoding="utf-8"))
_APEX_PATHS = {"challengerleagues": "CHALLENGER", "grandmasterleagues": "GRANDMASTER", "masterleagues": "MASTER"}


class FakeRiot:
    def __init__(
        self,
        key: str,
        *,
        platforms: tuple[str, ...] = ("EUN1",),
        players_per_tier: int | dict[str, int] = 4,
        matches_per_player: int = 12,
    ) -> None:
        self.valid_keys = {key}
        self.requests: list[httpx.Request] = []
        self.paths = Counter()
        self.unranked: set[str] = set()
        self.fail: dict[str, int] = {}  # regex on path -> status code to answer
        self.players: dict[tuple[str, str], list[str]] = {}
        self.histories: dict[str, list[str]] = {}
        self._participants: dict[str, list[str]] = {}
        for platform in platforms:
            for tier in TIERS:
                n = players_per_tier.get(tier, 0) if isinstance(players_per_tier, dict) else players_per_tier
                puuids = [f"{platform}-{tier}-{i}" for i in range(n)]
                self.players[(platform, tier)] = puuids
                for puuid in puuids:
                    self.set_history(puuid, [_match_id(platform, puuid, j) for j in range(matches_per_player)])

    # ----------------------------------------------------------------- world setup

    def set_history(self, puuid: str, match_ids: list[str]) -> None:
        self.histories[puuid] = list(match_ids)
        for match_id in match_ids:
            players = self._participants.setdefault(match_id, [])
            if puuid not in players:
                players.append(puuid)

    def participants(self, match_id: str) -> list[str]:
        players = list(self._participants.get(match_id, []))
        return players + [f"x-{match_id}-{j}" for j in range(10 - len(players))]

    def count(self, pattern: str) -> int:
        """Number of requests whose path matches `pattern`."""
        return sum(n for path, n in self.paths.items() if re.search(pattern, path))

    # ----------------------------------------------------------------- HTTP

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        self.paths[path] += 1
        if request.headers.get("X-Riot-Token") not in self.valid_keys:
            return _error(403, "Forbidden")
        for pattern, status in self.fail.items():
            if re.search(pattern, path):
                return _error(status, "injected failure")
        platform = request.url.host.split(".")[0].upper()
        params = request.url.params

        if path == "/lol/status/v4/platform-data":
            return httpx.Response(200, json={"id": platform, "maintenances": [], "incidents": []})
        if m := re.fullmatch(r"/lol/league/v4/entries/RANKED_SOLO_5x5/(\w+)/(\w+)", path):
            return self._entries_page(platform, m[1], m[2], int(params.get("page", 1)))
        if m := re.fullmatch(r"/lol/league/v4/(\w+)/by-queue/RANKED_SOLO_5x5", path):
            tier = _APEX_PATHS[m[1]]
            entries = [self._entry(p, tier, "I") for p in self.players.get((platform, tier), [])]
            return httpx.Response(200, json={"tier": tier, "queue": "RANKED_SOLO_5x5", "entries": entries})
        if m := re.fullmatch(r"/lol/league/v4/entries/by-puuid/(.+)", path):
            puuid = m[1]
            body = [] if puuid in self.unranked else [self._entry(puuid, "GOLD", "II")]
            return httpx.Response(200, json=body)
        if m := re.fullmatch(r"/lol/match/v5/matches/by-puuid/(.+)/ids", path):
            history = self.histories.get(m[1], [])
            start, count = int(params.get("start", 0)), int(params.get("count", 20))
            return httpx.Response(200, json=history[start : start + count])
        if m := re.fullmatch(r"/lol/match/v5/matches/([^/]+)/timeline", path):
            meta = {"matchId": m[1], "participants": self.participants(m[1])}
            return httpx.Response(200, json={"metadata": meta, "info": {"frames": []}})
        if m := re.fullmatch(r"/lol/match/v5/matches/([^/]+)", path):
            return httpx.Response(200, json=self._match(m[1]))
        if m := re.fullmatch(r"/riot/account/v1/accounts/by-riot-id/([^/]+)/([^/]+)", path):
            return httpx.Response(200, json={"puuid": f"seed-{m[1]}", "gameName": m[1], "tagLine": m[2]})
        return _error(404, "Not found")

    def _entries_page(self, platform: str, tier: str, division: str, page: int) -> httpx.Response:
        assert tier not in APEX_TIERS
        players = [p for i, p in enumerate(self.players.get((platform, tier), [])) if DIVISIONS[i % 4] == division]
        chunk = players[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
        return httpx.Response(200, json=[self._entry(p, tier, division) | {"queueType": "RANKED_SOLO_5x5"} for p in chunk])

    @staticmethod
    def _entry(puuid: str, tier: str, division: str) -> dict:
        return {
            "puuid": puuid,
            "queueType": "RANKED_SOLO_5x5",
            "tier": tier,
            "rank": division,
            "leaguePoints": 50,
            "wins": 10,
            "losses": 9,
        }

    def _match(self, match_id: str) -> dict:
        data = copy.deepcopy(_MATCH_TEMPLATE)
        participants = self.participants(match_id)
        data["metadata"]["matchId"] = match_id
        data["metadata"]["participants"] = participants
        for participant, puuid in zip(data["info"]["participants"], participants):
            participant["puuid"] = puuid
        data["info"]["platformId"] = match_id.split("_")[0]
        return data


def _match_id(platform: str, puuid: str, j: int) -> str:
    return f"{platform}_{zlib.crc32(puuid.encode())}{j:03d}"


def _error(status: int, message: str) -> httpx.Response:
    return httpx.Response(status, json={"status": {"message": message, "status_code": status}})
