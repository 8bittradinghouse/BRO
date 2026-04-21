from __future__ import annotations

import json
import logging
import os
import pathlib
import tempfile
from typing import Any, Dict

LOG = logging.getLogger(__name__)
_DIR_FSYNC_WARNING_EMITTED = False


def load_state(path: pathlib.Path) -> Dict[str, Any]:
    if not path.exists():
        return {
            "positions": {},
            "seen_trade_ids": [],
            "last_fill_ts_utc": None,
            "last_status_ts_utc": None,
        }
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("state file must contain a JSON object")
    data.setdefault("positions", {})
    data.setdefault("seen_trade_ids", [])
    data.setdefault("last_fill_ts_utc", None)
    data.setdefault("last_status_ts_utc", None)
    return data


def save_state(path: pathlib.Path, data: Dict[str, Any]) -> None:
    global _DIR_FSYNC_WARNING_EMITTED
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".state_", suffix=".json", dir=str(path.parent))
    tmp_path = pathlib.Path(tmp_name)
    try:
        with open(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=True, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        tmp_path.replace(path)
        # Best-effort directory sync for stronger crash consistency.
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError as exc:
            if not _DIR_FSYNC_WARNING_EMITTED:
                LOG.warning("state_store_dir_fsync_failed:%s", exc.__class__.__name__)
                _DIR_FSYNC_WARNING_EMITTED = True
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
