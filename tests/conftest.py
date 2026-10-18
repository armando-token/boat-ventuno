"""Shared pytest configuration: board-touching tests are opt-in."""

from __future__ import annotations

import os

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-board",
        action="store_true",
        default=False,
        help="run tests marked 'board' (SSH to the Linux edge board)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    if config.getoption("--run-board") or os.environ.get("SDV_RUN_BOARD") == "1":
        return
    skip = pytest.mark.skip(reason="board test disabled; use --run-board or SDV_RUN_BOARD=1")
    for item in items:
        if "board" in item.keywords:
            item.add_marker(skip)
