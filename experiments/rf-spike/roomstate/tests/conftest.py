"""fsync costs ~20 ms per state write on this disk; the tests check atomic replacement and content, not power-loss
durability, so they skip it."""

import os

import pytest


@pytest.fixture(autouse=True)
def _no_fsync(monkeypatch):
    monkeypatch.setattr(os, "fsync", lambda fd: None)
