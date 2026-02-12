"""Utility methods."""

import uuid
from collections.abc import Callable
from typing import Optional

def call_all(*callables: Optional[Callable[[], None]]) -> None:
    for item in filter(None, callables):
        item()
