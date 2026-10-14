"""Crawler settings loaded from `.env` and the environment (DESIGN §4)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from datetime import date
from pathlib import Path
from typing import Callable

from dotenv import dotenv_values

# Platform routing value -> regional routing value used by match-v5 / account-v1 (DESIGN §5).
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

STRATEGIES = ("stratified", "snowball")

# Variable name -> default value as it would appear in `.env`.
DEFAULTS: dict[str, str] = {
    "RIOT_API_KEY": "",
    "REGIONS": "eun1,na1,kr",
    "QUEUE_ID": "420",
    "WINDOW_DAYS": "14",
    "SPLIT_RESET_DATE": "",
    "FETCH_TIMELINES": "true",
    "MATCHES_PER_PLAYER": "5",
    "STRATEGY": "stratified",
    "SEED_RIOT_ID": "",
    "RANK_REUSE_HOURS": "6",
    "RATE_LIMIT_SAFETY": "0.9",
    "KEY_POLL_SECONDS": "30",
    "MAX_RETRIES": "5",
    "OFFLINE_AFTER_SECONDS": "300",
    "MIN_FREE_DISK_GB": "2",
    "RAW_ROLL_MB": "128",
    "DATA_DIR": "data",
    "LOG_DIR": "logs",
}

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class ConfigError(ValueError):
    """Raised when settings are missing or invalid; lists every problem found."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Invalid configuration:\n" + "\n".join(f"  - {p}" for p in problems))


@dataclass(frozen=True)
class Settings:
    """All crawler settings. The API key is excluded from `repr`."""

    riot_api_key: str = field(repr=False)
    regions: tuple[str, ...]
    queue_id: int
    window_days: int
    split_reset_date: date | None
    fetch_timelines: bool
    matches_per_player: int
    strategy: str
    seed_riot_id: str | None
    rank_reuse_hours: float
    rate_limit_safety: float
    key_poll_seconds: float
    max_retries: int
    offline_after_seconds: float
    min_free_disk_gb: float
    raw_roll_mb: int
    data_dir: Path
    log_dir: Path

    @property
    def masked_key(self) -> str:
        """Key representation that is safe to print or log."""
        return "RGAPI-****" if self.riot_api_key else "(not set)"

    def describe(self) -> list[tuple[str, str]]:
        """(name, value) pairs for display, with the key masked."""
        rows = []
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name == "riot_api_key":
                text = self.masked_key
            elif isinstance(value, bool):
                text = "true" if value else "false"
            elif isinstance(value, tuple):
                text = ",".join(value)
            elif value is None:
                text = "(not set)"
            else:
                text = str(value)
            rows.append((f.name.upper(), text))
        return rows


def load_settings(env_file: Path = Path(".env"), *, require_key: bool = True) -> Settings:
    """Read `env_file` (fresh on every call) plus `os.environ` overrides into `Settings`.

    Re-reading on every call lets the HTTP client pick up a swapped key. A variable set
    in the process environment wins over the file; empty values fall back to defaults.
    """
    raw = dict(DEFAULTS)
    if env_file.is_file():
        for name, value in dotenv_values(env_file).items():
            if name in DEFAULTS and value is not None and value.strip():
                raw[name] = value.strip()
    for name in DEFAULTS:
        value = os.environ.get(name)
        if value is not None and value.strip():
            raw[name] = value.strip()

    problems: list[str] = []
    parse = _Parser(raw, problems)

    key = raw["RIOT_API_KEY"]
    if require_key and not key:
        problems.append(f"RIOT_API_KEY is not set (add it to {env_file} - see .env.example)")

    regions = parse.regions("REGIONS")
    strategy = raw["STRATEGY"].lower()
    if strategy not in STRATEGIES:
        problems.append(f"STRATEGY must be one of {', '.join(STRATEGIES)}, got {strategy!r}")

    seed_riot_id = raw["SEED_RIOT_ID"] or None
    if seed_riot_id is not None and not _is_riot_id(seed_riot_id):
        problems.append(f"SEED_RIOT_ID must look like GameName#TAG, got {seed_riot_id!r}")
    if strategy == "snowball" and seed_riot_id is None:
        problems.append("STRATEGY=snowball requires SEED_RIOT_ID (GameName#TAG)")

    settings_kwargs = dict(
        riot_api_key=key,
        regions=regions,
        queue_id=parse.number("QUEUE_ID", int, minimum=0),
        window_days=parse.number("WINDOW_DAYS", int, minimum=1),
        split_reset_date=parse.optional_date("SPLIT_RESET_DATE"),
        fetch_timelines=parse.boolean("FETCH_TIMELINES"),
        matches_per_player=parse.number("MATCHES_PER_PLAYER", int, minimum=1, maximum=100),
        strategy=strategy,
        seed_riot_id=seed_riot_id,
        rank_reuse_hours=parse.number("RANK_REUSE_HOURS", float, minimum=0),
        rate_limit_safety=parse.number("RATE_LIMIT_SAFETY", float, minimum=0.01, maximum=1),
        key_poll_seconds=parse.number("KEY_POLL_SECONDS", float, minimum=1),
        max_retries=parse.number("MAX_RETRIES", int, minimum=1),
        offline_after_seconds=parse.number("OFFLINE_AFTER_SECONDS", float, minimum=1),
        min_free_disk_gb=parse.number("MIN_FREE_DISK_GB", float, minimum=0),
        raw_roll_mb=parse.number("RAW_ROLL_MB", int, minimum=1),
        data_dir=Path(raw["DATA_DIR"]),
        log_dir=Path(raw["LOG_DIR"]),
    )
    if problems:
        raise ConfigError(problems)
    return Settings(**settings_kwargs)


def _is_riot_id(value: str) -> bool:
    name, sep, tag = value.rpartition("#")
    return bool(sep and name.strip() and tag.strip())


class _Parser:
    """Typed parsing of raw string values that collects problems instead of raising."""

    def __init__(self, raw: dict[str, str], problems: list[str]) -> None:
        self.raw = raw
        self.problems = problems

    def number[T: (int, float)](
        self,
        name: str,
        kind: Callable[[str], T],
        *,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> T:
        text = self.raw[name]
        try:
            value = kind(text)
        except ValueError:
            self.problems.append(f"{name} must be {'an integer' if kind is int else 'a number'}, got {text!r}")
            return kind(DEFAULTS[name])
        if minimum is not None and value < minimum:
            self.problems.append(f"{name} must be >= {minimum}, got {value}")
        if maximum is not None and value > maximum:
            self.problems.append(f"{name} must be <= {maximum}, got {value}")
        return value

    def boolean(self, name: str) -> bool:
        text = self.raw[name].lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        self.problems.append(f"{name} must be true or false, got {self.raw[name]!r}")
        return DEFAULTS[name] in _TRUE

    def optional_date(self, name: str) -> date | None:
        text = self.raw[name]
        if not text:
            return None
        try:
            return date.fromisoformat(text)
        except ValueError:
            self.problems.append(f"{name} must be an ISO date (YYYY-MM-DD), got {text!r}")
            return None

    def regions(self, name: str) -> tuple[str, ...]:
        items = [item.strip().lower() for item in self.raw[name].split(",")]
        regions = tuple(dict.fromkeys(item for item in items if item))
        unknown = [r for r in regions if r not in PLATFORM_TO_REGION]
        if unknown:
            self.problems.append(
                f"{name} contains unknown platform(s) {', '.join(unknown)}; "
                f"known: {', '.join(PLATFORM_TO_REGION)}"
            )
        if not regions:
            self.problems.append(f"{name} must list at least one platform")
        return regions
