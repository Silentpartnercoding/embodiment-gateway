"""Hardware-neutral protocol commands for the Embodiment Gateway."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any

from .gateway import RootJudgment, verify_embodiment_receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="embodiment-gateway")
    commands = parser.add_subparsers(dest="command", required=True)
    wrap = commands.add_parser(
        "wrap-judgment",
        help="wrap a provider-neutral root verdict in the canonical handoff contract",
    )
    wrap.add_argument("verdict", type=Path)
    wrap.add_argument("--subject-id", required=True)
    wrap.add_argument("--issuer", required=True)
    wrap.add_argument("--issued-at")
    wrap.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser(
        "verify-receipt", help="verify a portable execution receipt and all digest bindings"
    )
    verify.add_argument("receipt", type=Path)
    contracts = commands.add_parser(
        "contracts", help="list the packaged canonical cross-project schemas"
    )
    contracts.add_argument("--output-dir", type=Path)
    return parser


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "wrap-judgment":
        judgment = RootJudgment.from_root_verdict(
            subject_id=args.subject_id,
            issuer=args.issuer,
            verdict_payload=_load(args.verdict),
            issued_at=args.issued_at or datetime.now(timezone.utc).isoformat(),
        )
        _write(args.output, judgment.payload())
        result = {"written": str(args.output), "judgment_digest": judgment.digest}
    elif args.command == "verify-receipt":
        result = verify_embodiment_receipt(_load(args.receipt))
    else:
        contract_root = files("embodiment_gateway").joinpath("contracts")
        names = sorted(item.name for item in contract_root.iterdir() if item.name.endswith(".json"))
        written: list[str] = []
        if args.output_dir:
            args.output_dir.mkdir(parents=True, exist_ok=True)
            for name in names:
                destination = args.output_dir / name
                destination.write_text(contract_root.joinpath(name).read_text(), encoding="utf-8")
                written.append(str(destination))
        result = {"contracts": names, "written": written}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
