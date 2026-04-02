#!/usr/bin/env python3
"""Create daily backup bundle with retention and integrity hash."""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import pathlib
import tarfile
from typing import Dict, List, Sequence, Tuple


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _add_path(
    tf: tarfile.TarFile,
    source: pathlib.Path,
    arc_prefix: str,
    *,
    exclude_globs: Sequence[str],
) -> Tuple[int, int]:
    if not source.exists():
        return 0, 0
    added = 0
    skipped_symlink = 0
    if source.is_file():
        if source.is_symlink():
            return 0, 1
        tf.add(source, arcname=f"{arc_prefix}/{source.name}")
        return 1, 0
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            skipped_symlink += 1
            continue
        if path.is_file():
            rel = path.relative_to(source)
            rel_text = rel.as_posix()
            if any(fnmatch.fnmatch(rel_text, pat) for pat in exclude_globs):
                continue
            tf.add(path, arcname=f"{arc_prefix}/{rel_text}")
            added += 1
    return added, skipped_symlink


def _prune_old_backups(backup_dir: pathlib.Path, keep_days: int) -> List[str]:
    removed: List[str] = []
    threshold = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=max(0, int(keep_days)))
    for path in sorted(backup_dir.glob("bro_backup_*.tar.gz")):
        mtime = dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.timezone.utc)
        if mtime < threshold:
            path.unlink(missing_ok=True)
            hash_path = path.with_suffix(path.suffix + ".sha256")
            hash_path.unlink(missing_ok=True)
            removed.append(str(path))
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro daily backup bundle")
    parser.add_argument("--log-dir", default="./logs_exec", help="Log directory")
    parser.add_argument("--state-path", default="./logs_exec/state.json", help="State file path")
    parser.add_argument("--manifest-glob", default="run_manifest_*.json", help="Manifest glob in log dir")
    parser.add_argument("--out-dir", default="./backups", help="Backup output directory")
    parser.add_argument("--keep-days", type=int, default=14, help="Retention window")
    parser.add_argument("--tag", default="", help="Optional tag appended to bundle name")
    parser.add_argument("--exclude-glob", action="append", default=[], help="Glob under log dir to exclude; can repeat")
    parser.add_argument("--require-files-min", type=int, default=1, help="Minimum files required in archive")
    parser.add_argument("--max-bytes", type=int, default=0, help="Fail if archive exceeds this size (0 disables)")
    args = parser.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    date_str = now.date().isoformat()
    tag = f"_{args.tag.strip()}" if str(args.tag).strip() else ""
    out_dir = pathlib.Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    log_dir = pathlib.Path(args.log_dir).resolve()
    state_path = pathlib.Path(args.state_path).resolve()

    bundle_name = f"bro_backup_{date_str}{tag}.tar.gz"
    bundle_path = out_dir / bundle_name
    metadata_path = out_dir / f"{bundle_name}.meta.json"
    hash_path = out_dir / f"{bundle_name}.sha256"

    files_added = 0
    symlink_skipped = 0
    manifests_added = 0
    state_added = False
    exclude_globs = [str(p).strip() for p in args.exclude_glob if str(p).strip()]
    with tarfile.open(bundle_path, "w:gz") as tf:
        added, skipped = _add_path(tf, log_dir, "logs", exclude_globs=exclude_globs)
        files_added += added
        symlink_skipped += skipped
        if state_path.exists() and state_path.is_file():
            tf.add(state_path, arcname="state/state.json")
            files_added += 1
            state_added = True
        for manifest in sorted(log_dir.glob(args.manifest_glob)):
            if manifest.is_file() and (not manifest.is_symlink()):
                tf.add(manifest, arcname=f"manifests/{manifest.name}")
                files_added += 1
                manifests_added += 1

    if int(args.require_files_min) > 0 and files_added < int(args.require_files_min):
        bundle_path.unlink(missing_ok=True)
        raise SystemExit(f"files_added_below_min:{files_added}<min:{int(args.require_files_min)}")

    if int(args.max_bytes) > 0 and bundle_path.stat().st_size > int(args.max_bytes):
        size = int(bundle_path.stat().st_size)
        bundle_path.unlink(missing_ok=True)
        raise SystemExit(f"bundle_size_exceeds_limit:{size}>max:{int(args.max_bytes)}")

    sha256 = _sha256_file(bundle_path)
    hash_path.write_text(f"{sha256}  {bundle_path.name}\n", encoding="utf-8")

    removed = _prune_old_backups(out_dir, keep_days=int(args.keep_days))
    metadata: Dict[str, object] = {
        "ts_utc": now.isoformat().replace("+00:00", "Z"),
        "bundle_path": str(bundle_path),
        "sha256": sha256,
        "files_added": files_added,
        "manifests_added": manifests_added,
        "state_added": state_added,
        "symlink_entries_skipped": symlink_skipped,
        "exclude_globs": exclude_globs,
        "retention_keep_days": int(args.keep_days),
        "pruned_backups": removed,
        "note": "Optional encryption is not performed by default; encrypt bundle before off-host transfer if needed.",
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    print(f"bundle={bundle_path}")
    print(f"sha256={sha256}")
    print(f"meta={metadata_path}")
    print(f"files_added={files_added}")
    print(f"pruned={len(removed)}")


if __name__ == "__main__":
    main()
