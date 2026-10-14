"""Raw JSON Lines storage, gzip-compressed and rolled by size (DESIGN §10).

Each record is written as its own complete gzip member and flushed + fsynced, so a crash
can at worst leave a half-written last member. Concatenated members are standard gzip
(`zcat`, Python `gzip`, Hadoop/Spark codecs read them as one stream). Before a writer opens
a new part it trims such a half-written tail from the newest existing part.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import zlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

log = logging.getLogger(__name__)

KINDS = ("matches", "timelines", "ranks")
_PART = re.compile(r"^part-(\d{4,})\.jsonl\.gz$")
_COMPRESS_LEVEL = 6
_SCAN_CHUNK = 1 << 16


def utc_now_iso() -> str:
    """Current time as ISO-8601 UTC with milliseconds, e.g. `2026-10-14T18:05:09.123Z`."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ----------------------------------------------------------------- envelopes (DESIGN §10)


def match_record(
    match_id: str,
    platform_id: str,
    data: dict[str, Any],
    *,
    seed_tier: str | None,
    seed_puuid: str | None,
    strategy: str,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    return {
        "match_id": match_id,
        "platform_id": platform_id,
        "fetched_at": fetched_at or utc_now_iso(),
        "seed_tier": seed_tier,
        "seed_puuid": seed_puuid,
        "strategy": strategy,
        "data": data,
    }


def timeline_record(match_id: str, platform_id: str, data: dict[str, Any], *, fetched_at: str | None = None) -> dict[str, Any]:
    return {"match_id": match_id, "platform_id": platform_id, "fetched_at": fetched_at or utc_now_iso(), "data": data}


def rank_record(
    puuid: str, platform_id: str, status: str, entries: list[dict[str, Any]], *, fetched_at: str | None = None
) -> dict[str, Any]:
    return {
        "puuid": puuid,
        "platform_id": platform_id,
        "fetched_at": fetched_at or utc_now_iso(),
        "status": status,
        "entries": entries,
    }


# ----------------------------------------------------------------- writing


class _Part:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.file: BinaryIO = open(path, "xb")  # never append to an existing part
        self.size = 0


class RawWriter:
    """Writes records of one kind into `root/<kind>/platform=<ID>/part-NNNN.jsonl.gz`."""

    def __init__(self, root: Path, kind: str, roll_mb: float = 128, *, fsync: bool = True) -> None:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")
        self.root = root
        self.kind = kind
        self.roll_bytes = int(roll_mb * 1024 * 1024)
        self.fsync = fsync
        self._parts: dict[str, _Part] = {}

    def platform_dir(self, platform_id: str) -> Path:
        return self.root / self.kind / f"platform={platform_id.upper()}"

    def write(self, platform_id: str, record: dict[str, Any]) -> Path:
        """Append one record and make it durable. Returns the part file written to."""
        platform_id = platform_id.upper()
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        member = gzip.compress(line.encode("utf-8"), compresslevel=_COMPRESS_LEVEL, mtime=0)
        part = self._parts.get(platform_id)
        if part is None or (part.size and part.size + len(member) > self.roll_bytes):
            if part is not None:
                part.file.close()
            part = self._parts[platform_id] = self._open_next(platform_id)
        part.file.write(member)
        part.file.flush()
        if self.fsync:
            os.fsync(part.file.fileno())
        part.size += len(member)
        return part.path

    def close(self) -> None:
        for part in self._parts.values():
            part.file.close()
        self._parts.clear()

    def __enter__(self) -> RawWriter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _open_next(self, platform_id: str) -> _Part:
        directory = self.platform_dir(platform_id)
        directory.mkdir(parents=True, exist_ok=True)
        existing = list_parts(directory)
        if existing:
            repair_tail(existing[-1])
        number = part_number(existing[-1]) + 1 if existing else 1
        path = directory / f"part-{number:04d}.jsonl.gz"
        log.debug("Opening %s", path)
        return _Part(path)


# ----------------------------------------------------------------- reading / repair


def part_number(path: Path) -> int:
    match = _PART.match(path.name)
    if match is None:
        raise ValueError(f"Not a part file: {path}")
    return int(match.group(1))


def list_parts(directory: Path) -> list[Path]:
    """Part files of one platform folder, in numeric order."""
    if not directory.is_dir():
        return []
    return sorted((p for p in directory.iterdir() if _PART.match(p.name)), key=part_number)


def _complete_length(path: Path) -> int:
    """Length in bytes of the leading complete gzip members of `path`."""
    complete = 0  # end of the last complete member
    with open(path, "rb") as file:
        decompressor = zlib.decompressobj(wbits=31)
        base = 0  # file offset of `chunk`
        chunk = file.read(_SCAN_CHUNK)
        while chunk:
            try:
                decompressor.decompress(chunk)
            except zlib.error:
                return complete
            if decompressor.eof:
                complete = base + len(chunk) - len(decompressor.unused_data)
                chunk, base = decompressor.unused_data, complete
                decompressor = zlib.decompressobj(wbits=31)
                if not chunk:
                    chunk = file.read(_SCAN_CHUNK)
            else:
                base += len(chunk)
                chunk = file.read(_SCAN_CHUNK)
    return complete


def repair_tail(path: Path) -> int:
    """Cut a half-written last record (crash mid-write) off `path`. Returns bytes removed."""
    size = path.stat().st_size
    good = _complete_length(path)
    if good < size:
        log.warning("%s ends with a damaged record (%d bytes) - trimming it", path, size - good)
        with open(path, "r+b") as file:
            file.truncate(good)
    return size - good


def read_records(path: Path) -> Iterator[dict[str, Any]]:
    """Yield records from a part file; a damaged tail is logged and skipped, not raised."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as file:
            for line in file:
                if line.strip():
                    yield json.loads(line)
    except (EOFError, zlib.error, gzip.BadGzipFile) as exc:
        log.warning("%s: stopped at damaged data (%s)", path, exc)
