from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Dict


from prodesk.common import utc_iso
from prodesk.config import load_execution_config
from prodesk.security import run_security_checks


def run_security_audit(*, config_path: pathlib.Path, mode_override: str | None = None) -> Dict[str, Any]:
    cfg = load_execution_config(config_path.resolve())
    mode = str(mode_override or cfg.get("mode", "paper")).lower().strip()
    findings = run_security_checks(cfg, mode=mode)
    return {
        "ts_utc": utc_iso(),
        "mode": mode,
        "config_path": str(config_path.resolve()),
        "ok": len(findings) == 0,
        "finding_count": len(findings),
        "findings": findings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro security posture audit")
    parser.add_argument("--config", default="execution_config.yaml", help="Execution config path")
    parser.add_argument("--mode", choices=["paper", "live"], default=None, help="Optional mode override")
    parser.add_argument("--out", default=None, help="Optional JSON output path")
    args = parser.parse_args()

    result = run_security_audit(config_path=pathlib.Path(args.config), mode_override=args.mode)
    output = json.dumps(result, indent=2, sort_keys=True)
    print(output)

    if args.out:
        out_path = pathlib.Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output + "\n", encoding="utf-8")

    raise SystemExit(0 if result["ok"] else 2)


if __name__ == "__main__":
    main()
