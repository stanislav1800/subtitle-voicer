"""Command line entry point: ``uv run python -m subvoicer.cli <command>`` (TZ 4.15).

Commands are added by later stages (watch — stage 2, run / characters — stage 5).
"""

from __future__ import annotations

import argparse
import logging

from subvoicer.app.log import setup_logging
from subvoicer.config.paths import AppPaths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="subvoicer", description="Subtitle Voicer (CLI)")
    parser.add_argument("--debug", action="store_true", help="подробный лог (DEBUG)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    paths = AppPaths.default()
    log_file = setup_logging(
        paths.logs_dir, level=logging.DEBUG if args.debug else logging.INFO, console=True
    )
    logging.getLogger("subvoicer").info("Лог: %s", log_file)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
