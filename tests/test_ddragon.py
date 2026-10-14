"""Tests for riot_client.ddragon (MockTransport, temp dirs)."""

from __future__ import annotations

import httpx
import pytest

from riot_client.ddragon import FILES, DataDragon, patch_of, pick_version
from tests.virtual_time import run

VERSIONS = ["16.20.1", "16.19.3", "16.19.12", "16.2.1", "15.24.1", "lolpatch_3.7", "0.151.2"]


def test_patch_of():
    assert patch_of("16.20.824.8524") == "16.20"
    assert patch_of("16.20.1") == "16.20"


def test_pick_version():
    assert pick_version(VERSIONS) == "16.20.1"
    assert pick_version(VERSIONS, "16.19") == "16.19.12"  # numeric, not string order
    assert pick_version(VERSIONS, "16.2") == "16.2.1"
    assert pick_version(VERSIONS, "9.9") is None


class Cdn:
    def __init__(self) -> None:
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        if request.url.path == "/api/versions.json":
            return httpx.Response(200, json=VERSIONS)
        assert "X-Riot-Token" not in request.headers
        return httpx.Response(200, json={"path": request.url.path})


def test_resolve_and_download(tmp_path):
    cdn = Cdn()

    async def scenario():
        async with DataDragon(transport=httpx.MockTransport(cdn)) as ddragon:
            version = await ddragon.resolve_version("16.19")
            first = await ddragon.download(version, tmp_path / version)
            second = await ddragon.download(version, tmp_path / version)
            return version, first, second

    version, first, second = run(scenario())
    assert version == "16.19.12"
    assert sorted(p.name for p in first) == sorted(FILES)
    assert second == []  # already present, nothing fetched again
    assert cdn.paths.count("/cdn/16.19.12/data/en_US/champion.json") == 1
    assert not list((tmp_path / version).glob("*.part"))


def test_unknown_patch_raises(tmp_path):
    async def scenario():
        async with DataDragon(transport=httpx.MockTransport(Cdn())) as ddragon:
            await ddragon.resolve_version("1.1")

    with pytest.raises(LookupError):
        run(scenario())
