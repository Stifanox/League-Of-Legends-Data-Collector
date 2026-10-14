"""Data Dragon static data (CDN, no key, no rate limit)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://ddragon.leagueoflegends.com"
FILES = ("champion.json", "item.json", "summoner.json", "runesReforged.json")
LOCALE = "en_US"
_ATTEMPTS = 3


def version_key(version: str) -> tuple[int, ...] | None:
    """`"15.19.1"` -> `(15, 19, 1)`; `None` for non-numeric entries like `"lolpatch_3.7"`."""
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return None


def patch_of(version: str) -> str:
    """Major.minor of a game or Data Dragon version: `"15.19.715.1234"` -> `"15.19"`."""
    return ".".join(version.split(".")[:2])


def pick_version(versions: list[str], patch: str | None = None) -> str | None:
    """Newest version overall, or the newest whose major.minor equals `patch`."""
    candidates = [
        (key, v) for v in versions if (key := version_key(v)) is not None and (patch is None or patch_of(v) == patch)
    ]
    return max(candidates)[1] if candidates else None


class DataDragon:
    """Small async client for the Data Dragon CDN."""

    def __init__(self, *, base_url: str = BASE_URL, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0), transport=transport)

    async def __aenter__(self) -> DataDragon:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._http.aclose()

    async def versions(self) -> list[str]:
        data = (await self._get(f"{self.base_url}/api/versions.json")).json()
        if not isinstance(data, list):
            raise ValueError("versions.json is not a list")
        return [v for v in data if isinstance(v, str)]

    async def resolve_version(self, patch: str | None = None) -> str:
        """Data Dragon version for `patch` (`"15.19"`), or the latest when `patch` is None."""
        version = pick_version(await self.versions(), patch)
        if version is None:
            raise LookupError(f"No Data Dragon version for patch {patch}")
        return version

    async def download(self, version: str, dest_dir: Path) -> list[Path]:
        """Download the static files of `version` into `dest_dir`; existing files are kept."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for name in FILES:
            target = dest_dir / name
            if target.exists():
                log.debug("Data Dragon %s/%s already present", version, name)
                continue
            response = await self._get(f"{self.base_url}/cdn/{version}/data/{LOCALE}/{name}")
            tmp = target.with_suffix(".part")
            tmp.write_bytes(response.content)
            tmp.replace(target)
            written.append(target)
        return written

    async def _get(self, url: str) -> httpx.Response:
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                response = await self._http.get(url)
                response.raise_for_status()
                return response
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                if attempt == _ATTEMPTS or (
                    isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500
                ):
                    raise
                log.warning("Data Dragon request failed (%s), retrying", type(exc).__name__)
                await asyncio.sleep(2 * attempt)
        raise AssertionError("unreachable")
