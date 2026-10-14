"""TSV and Parquet writers driven by the table schemas (DESIGN §11).

Output: `<root>/<format>/<table>/platform_id=<ID>/part-NNNN.<ext>` for per-platform tables and
`<root>/<format>/<table>/part-NNNN.<ext>` for Data Dragon dimensions.

`platform_id` is a column in TSV files but **not inside Parquet files**: it is the Hive
partition folder, which Spark, pyarrow and pandas turn back into a column when reading the
table directory. (pyarrow refuses a directory whose files also contain the partition column.)
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from crawler.export.schemas import Table

FORMATS = ("tsv", "parquet")
PARTITION_COLUMN = "platform_id"

TSV_ROLL_BYTES = 128 * 1024 * 1024
PARQUET_BATCH_ROWS = 50_000
PARQUET_ROWS_PER_FILE = 10_000_000
PARQUET_COMPRESSION = "snappy"

_TSV_ESCAPES = str.maketrans({"\t": " ", "\r": " ", "\n": " "})


def table_dir(root: Path, fmt: str, table: Table, platform_id: str | None) -> Path:
    directory = root / fmt / table.name
    if table.partitioned:
        directory = directory / f"{PARTITION_COLUMN}={platform_id}"
    return directory


# ----------------------------------------------------------------- TSV


def tsv_value(value: Any, type_: str) -> str:
    """One TSV cell: null -> empty, bool -> true/false, ts -> ISO UTC, list -> comma-joined."""
    if value is None:
        return ""
    if type_ == "bool":
        return "true" if value else "false"
    if type_ == "ts":
        return datetime.fromtimestamp(value / 1000, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if type_.startswith("list_"):
        return ",".join(str(v).translate(_TSV_ESCAPES).replace(",", " ") for v in value)
    if type_ == "float":
        return repr(float(value))
    return str(value).translate(_TSV_ESCAPES)


class TsvWriter:
    """Header + tab-separated rows, rolled at about `roll_bytes`."""

    def __init__(self, directory: Path, table: Table, *, roll_bytes: int = TSV_ROLL_BYTES) -> None:
        self.directory = directory
        self.table = table
        self.roll_bytes = roll_bytes
        self.rows = 0
        self._part = 0
        self._file = None
        self._size = 0
        self._header = "\t".join(table.column_names) + "\n"

    def write(self, row: dict[str, Any]) -> None:
        line = "\t".join(tsv_value(row.get(c.name), c.type) for c in self.table.columns) + "\n"
        data = line.encode("utf-8")
        if self._file is None or self._size + len(data) > self.roll_bytes:
            self._open_next()
        self._file.write(data)
        self._size += len(data)
        self.rows += 1

    def _open_next(self) -> None:
        if self._file is not None:
            self._file.close()
        self._part += 1
        self.directory.mkdir(parents=True, exist_ok=True)
        self._file = open(self.directory / f"part-{self._part:04d}.tsv", "wb")
        header = self._header.encode("utf-8")
        self._file.write(header)
        self._size = len(header)

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


# ----------------------------------------------------------------- Parquet


class ParquetTableWriter:
    """Buffers rows column-wise and writes row groups of `batch_rows`."""

    def __init__(
        self,
        directory: Path,
        table: Table,
        *,
        batch_rows: int = PARQUET_BATCH_ROWS,
        rows_per_file: int = PARQUET_ROWS_PER_FILE,
    ) -> None:
        self.directory = directory
        self.table = table
        self.batch_rows = batch_rows
        self.rows_per_file = rows_per_file
        self.rows = 0
        drop = (PARTITION_COLUMN,) if table.partitioned else ()
        self._columns = [c for c in table.columns if c.name not in drop]
        self._schema = table.arrow_schema(without=drop)
        self._buffer: dict[str, list[Any]] = {c.name: [] for c in self._columns}
        self._buffered = 0
        self._writer: pq.ParquetWriter | None = None
        self._part = 0
        self._file_rows = 0

    def write(self, row: dict[str, Any]) -> None:
        for column in self._columns:
            self._buffer[column.name].append(row.get(column.name))
        self._buffered += 1
        self.rows += 1
        if self._buffered >= self.batch_rows:
            self._flush()

    def _flush(self) -> None:
        if not self._buffered:
            return
        if self._writer is None or self._file_rows >= self.rows_per_file:
            self._open_next()
        batch = pa.Table.from_pydict(self._buffer, schema=self._schema)
        self._writer.write_table(batch)
        self._file_rows += self._buffered
        self._buffer = {c.name: [] for c in self._columns}
        self._buffered = 0

    def _open_next(self) -> None:
        if self._writer is not None:
            self._writer.close()
        self._part += 1
        self._file_rows = 0
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"part-{self._part:04d}.parquet"
        self._writer = pq.ParquetWriter(path, self._schema, compression=PARQUET_COMPRESSION)

    def close(self) -> None:
        self._flush()
        if self._writer is not None:
            self._writer.close()
            self._writer = None


# ----------------------------------------------------------------- all formats


class ExportWriters:
    """Sends every row to each enabled format; one writer per (format, table, platform)."""

    def __init__(self, root: Path, formats: tuple[str, ...], **options: Any) -> None:
        unknown = set(formats) - set(FORMATS)
        if unknown:
            raise ValueError(f"Unknown format(s) {sorted(unknown)}; use {', '.join(FORMATS)}")
        self.root = root
        self.formats = formats
        self.options = options
        self._writers: dict[tuple[str, str, str | None], TsvWriter | ParquetTableWriter] = {}
        self.row_counts: Counter[tuple[str, str | None]] = Counter()

    def write(self, table: Table, platform_id: str | None, row: dict[str, Any]) -> None:
        key_platform = platform_id if table.partitioned else None
        for fmt in self.formats:
            key = (fmt, table.name, key_platform)
            writer = self._writers.get(key)
            if writer is None:
                directory = table_dir(self.root, fmt, table, key_platform)
                if fmt == "tsv":
                    writer = TsvWriter(directory, table, **_pick(self.options, "roll_bytes"))
                else:
                    writer = ParquetTableWriter(directory, table, **_pick(self.options, "batch_rows", "rows_per_file"))
                self._writers[key] = writer
            writer.write(row)
        self.row_counts[(table.name, key_platform)] += 1

    def close(self) -> None:
        for writer in self._writers.values():
            writer.close()
        self._writers.clear()


def _pick(options: dict[str, Any], *names: str) -> dict[str, Any]:
    return {name: options[name] for name in names if name in options}
