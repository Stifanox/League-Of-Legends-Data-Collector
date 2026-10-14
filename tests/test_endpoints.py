"""Tests for riot_client.endpoints and riot_client.routing (fixtures + MockTransport)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from riot_client.endpoints import account, league, match
from riot_client.errors import InvalidResponse
from riot_client.http import RiotHttpClient
from riot_client.keys import KeyProvider
from riot_client.routing import account_region_of, host_url, platform_id, region_of
from tests.conftest import FIXTURES_DIR
from tests.virtual_time import run

KEY = "RGAPI-12345678-abcd-ef01-2345-6789abcdef01"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


MATCH = fixture("match.json")
MATCH_ID = MATCH["metadata"]["matchId"]
PUUID = MATCH["metadata"]["participants"][0]


class Api:
    """MockTransport serving fixtures by path; records requests."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = self.routes.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"status": {"message": "Not found", "status_code": 404}})
        return httpx.Response(200, json=body, headers={"X-Method-Rate-Limit": "2000:10"})

    def client(self) -> RiotHttpClient:
        keys = KeyProvider(KEY, lambda: KEY, notify=False)
        return RiotHttpClient(keys, probe_host="eun1", transport=httpx.MockTransport(self))


def call(api: Api, fn, *args, **kwargs):
    async def scenario():
        async with api.client() as client:
            return await fn(client, *args, **kwargs), client

    return run(scenario())


# ---------------------------------------------------------------- routing


def test_routing():
    assert region_of("eun1") == "europe"
    assert region_of("NA1") == "americas"
    assert region_of("kr") == "asia"
    assert region_of("vn2") == "sea"
    assert account_region_of("vn2") == "asia"
    assert account_region_of("eun1") == "europe"
    assert host_url("EUN1") == "https://eun1.api.riotgames.com"
    assert platform_id("eun1") == "EUN1"
    with pytest.raises(ValueError):
        region_of("xx1")


# ---------------------------------------------------------------- match-v5


def test_match_ids_host_path_params_and_method_key():
    api = Api({f"/lol/match/v5/matches/by-puuid/{PUUID}/ids": [MATCH_ID]})
    ids, client = call(
        api, match.match_ids_by_puuid, "europe", PUUID, queue=420, start_time=100, end_time=200, start=5, count=5
    )
    assert ids == [MATCH_ID]
    request = api.requests[0]
    assert request.url.host == "europe.api.riotgames.com"
    assert dict(request.url.params) == {"start": "5", "count": "5", "queue": "420", "startTime": "100", "endTime": "200"}
    assert "match-v5.ids" in client.limiters.get("europe").methods


def test_match_ids_count_validated():
    api = Api({})
    with pytest.raises(ValueError):
        call(api, match.match_ids_by_puuid, "europe", PUUID, count=101)
    with pytest.raises(ValueError):
        call(api, match.match_ids_by_puuid, "europe", PUUID, count=0)
    assert api.requests == []


def test_match_and_timeline():
    api = Api({
        f"/lol/match/v5/matches/{MATCH_ID}": MATCH,
        f"/lol/match/v5/matches/{MATCH_ID}/timeline": fixture("timeline.json"),
    })
    data, client = call(api, match.match, "europe", MATCH_ID)
    assert data == MATCH
    timeline, client2 = call(api, match.timeline, "europe", MATCH_ID)
    assert timeline["metadata"]["matchId"] == MATCH_ID
    assert [r.url.host for r in api.requests] == ["europe.api.riotgames.com"] * 2
    assert "match-v5.match" in client.limiters.get("europe").methods
    assert "match-v5.timeline" in client2.limiters.get("europe").methods


def test_unexpected_shape_raises_invalid_response():
    api = Api({f"/lol/match/v5/matches/{MATCH_ID}": {"oops": 1}})
    with pytest.raises(InvalidResponse):
        call(api, match.match, "europe", MATCH_ID)


# ---------------------------------------------------------------- league-v4


def test_entries_by_puuid_on_platform_host():
    api = Api({f"/lol/league/v4/entries/by-puuid/{PUUID}": fixture("entries_by_puuid_ranked.json")})
    entries, client = call(api, league.entries_by_puuid, "eun1", PUUID)
    assert api.requests[0].url.host == "eun1.api.riotgames.com"
    assert "league-v4.by-puuid" in client.limiters.get("eun1").methods
    assert league.solo_queue_rank(entries)["status"] == "RANKED"


def test_entries_page_path_and_params():
    api = Api({"/lol/league/v4/entries/RANKED_SOLO_5x5/GOLD/II": fixture("league_entries_page.json")})
    entries, client = call(api, league.entries_page, "eun1", "gold", "ii", page=3)
    assert len(entries) == 5
    assert dict(api.requests[0].url.params) == {"page": "3"}
    assert "league-v4.entries" in client.limiters.get("eun1").methods


@pytest.mark.parametrize(("tier", "division"), [("MASTER", "I"), ("GOLD", "V"), ("WOOD", "I")])
def test_entries_page_rejects_bad_tier_or_division(tier, division):
    with pytest.raises(ValueError):
        call(Api({}), league.entries_page, "eun1", tier, division)


@pytest.mark.parametrize(
    ("tier", "path"),
    [
        ("CHALLENGER", "/lol/league/v4/challengerleagues/by-queue/RANKED_SOLO_5x5"),
        ("grandmaster", "/lol/league/v4/grandmasterleagues/by-queue/RANKED_SOLO_5x5"),
        ("MASTER", "/lol/league/v4/masterleagues/by-queue/RANKED_SOLO_5x5"),
    ],
)
def test_apex_league_paths(tier, path):
    api = Api({path: fixture("apex_league.json")})
    data, client = call(api, league.apex_league, "kr", tier)
    assert api.requests[0].url.host == "kr.api.riotgames.com"
    assert len(data["entries"]) == 5
    assert "league-v4.apex" in client.limiters.get("kr").methods


def test_apex_league_rejects_division_tier():
    with pytest.raises(ValueError):
        call(Api({}), league.apex_league, "eun1", "DIAMOND")


def test_solo_queue_rank_ranked_picks_solo_entry():
    entries = fixture("entries_by_puuid_ranked.json")
    solo = next(e for e in entries if e["queueType"] == "RANKED_SOLO_5x5")
    assert league.solo_queue_rank(entries) == {
        "status": "RANKED",
        "tier": solo["tier"],
        "division": solo["rank"],
        "lp": solo["leaguePoints"],
        "wins": solo["wins"],
        "losses": solo["losses"],
    }


def test_solo_queue_rank_flex_only_is_unranked():
    entries = [e for e in fixture("entries_by_puuid_ranked.json") if e["queueType"] != "RANKED_SOLO_5x5"]
    assert entries  # fixture has other queues
    assert league.solo_queue_rank(entries)["status"] == "UNRANKED"


def test_solo_queue_rank_empty_is_unranked():
    rank = league.solo_queue_rank(fixture("entries_by_puuid_empty.json"))
    assert rank == {"status": "UNRANKED", "tier": None, "division": None, "lp": None, "wins": None, "losses": None}


def test_league_puuids_from_both_shapes():
    page = fixture("league_entries_page.json")
    apex = fixture("apex_league.json")
    assert league.league_puuids(page) == [e["puuid"] for e in page]
    assert league.league_puuids(apex) == [e["puuid"] for e in apex["entries"]]
    assert league.league_puuids([{"rank": "I"}, {"puuid": ""}, {"puuid": "p1"}]) == ["p1"]
    assert all(league.league_puuids(page))


# ---------------------------------------------------------------- account-v1


def test_parse_riot_id():
    assert account.parse_riot_id("Some Name#EUNE") == ("Some Name", "EUNE")
    assert account.parse_riot_id(" a#b#TAG ") == ("a#b", "TAG")
    for bad in ("NoTag", "#TAG", "Name#", ""):
        with pytest.raises(ValueError):
            account.parse_riot_id(bad)


def test_account_by_riot_id_quotes_name():
    acc = fixture("account.json")
    api = Api({"/riot/account/v1/accounts/by-riot-id/Some Name/EUNE": acc})
    data, client = call(api, account.account_by_riot_id, "europe", "Some Name", "EUNE")
    assert data == acc
    assert api.requests[0].url.raw_path == b"/riot/account/v1/accounts/by-riot-id/Some%20Name/EUNE"
    assert "account-v1.by-riot-id" in client.limiters.get("europe").methods
