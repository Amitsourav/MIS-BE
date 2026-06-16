"""Shared schema primitives."""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Standard paginated envelope: { items, total, page, page_size }."""

    items: list[T]
    total: int
    page: int
    page_size: int
