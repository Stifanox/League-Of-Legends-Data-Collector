"""Routing values, host names and ranked tier constants (DESIGN §5)."""

from __future__ import annotations

# Platform routing value -> regional routing value used by match-v5 / account-v1.
PLATFORM_TO_REGION: dict[str, str] = {
    "eun1": "europe",
    "euw1": "europe",
    "tr1": "europe",
    "ru": "europe",
    "me1": "europe",
    "na1": "americas",
    "br1": "americas",
    "la1": "americas",
    "la2": "americas",
    "kr": "asia",
    "jp1": "asia",
    "oc1": "sea",
    "sg2": "sea",
    "tw2": "sea",
    "vn2": "sea",
}
REGIONS = frozenset(PLATFORM_TO_REGION.values())

# account-v1 is served by americas/asia/europe only (no sea).
ACCOUNT_REGION: dict[str, str] = {"europe": "europe", "americas": "americas", "asia": "asia", "sea": "asia"}

SOLO_QUEUE = "RANKED_SOLO_5x5"
SOLO_QUEUE_ID = 420

DIVISION_TIERS = ("IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND")
APEX_TIERS = ("MASTER", "GRANDMASTER", "CHALLENGER")
TIERS = DIVISION_TIERS + APEX_TIERS
DIVISIONS = ("I", "II", "III", "IV")


def region_of(platform: str) -> str:
    """Regional routing value for a platform, e.g. `eun1 -> europe`."""
    try:
        return PLATFORM_TO_REGION[platform.lower()]
    except KeyError:
        raise ValueError(f"Unknown platform {platform!r}") from None


def account_region_of(platform: str) -> str:
    """Regional host serving account-v1 for players of `platform`."""
    return ACCOUNT_REGION[region_of(platform)]


def host_url(routing_value: str) -> str:
    """Base URL of a platform or regional host."""
    return f"https://{routing_value.lower()}.api.riotgames.com"


def platform_id(platform: str) -> str:
    """Upper-case platform id as used in match ids and raw folders, e.g. `EUN1`."""
    return platform.upper()
