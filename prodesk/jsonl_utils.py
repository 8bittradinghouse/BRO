from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, List

DEFAULT_MAX_LINES_PER_FILE = 200000


def tail_lines(path: pathlib.Path, *, limit: int) -> List[str]:
    max_lines = max(0, int(limit))
    if max_lines <= 0:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            return [line.rstrip("\n") for line in fh]

    with path.open("rb") as fh:
        fh.seek(0, 2)
        file_size = fh.tell()
        block_size = 64 * 1024
        data = b""
        pos = file_size
        newline_count = 0

        while pos > 0 and newline_count <= max_lines:
            read_size = min(block_size, pos)
            pos -= read_size
            fh.seek(pos)
            chunk = fh.read(read_size)
            data = chunk + data
            newline_count = data.count(b"\n")

    lines = data.splitlines()[-max_lines:]
    return [line.decode("utf-8", errors="ignore") for line in lines]


def load_jsonl(
    paths: List[pathlib.Path],
    *,
    max_lines_per_file: int = DEFAULT_MAX_LINES_PER_FILE,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    limit = max(0, int(max_lines_per_file))
    for path in paths:
        lines = tail_lines(path, limit=limit) if limit > 0 else tail_lines(path, limit=0)
        for line in lines:
            text = line.strip()
            if not text:
                continue
            try:
                rec = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                rows.append(rec)
    return rows
