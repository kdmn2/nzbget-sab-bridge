"""Category resolution for SAB jobs.

SABnzbd represents the default category as ``*``. Its source maps the literal
labels ``""``, ``"none"``, and ``"default"`` to ``*`` during change/add
operations. We use the actual marker returned by ``get_cats`` rather than
assuming a real category named "Default" exists.
"""

from __future__ import annotations

from .status import normalize_category


def resolve_category(requested: str, categories: list[str]) -> str:
    if "*" not in categories:
        raise ValueError("SABnzbd did not report a default category marker")

    if requested == "" or requested == "*" or requested.casefold() == "default":
        return "*"

    real = [category for category in categories if category != "*"]
    if requested in real and requested.casefold() != "default":
        return requested

    matches = [
        category
        for category in real
        if category.casefold() == requested.casefold() and category.casefold() != "default"
    ]
    if len(matches) == 1:
        return matches[0]
    raise ValueError("Category is not configured in SABnzbd")


def category_matches(actual: object, target: str) -> bool:
    normalized = normalize_category(actual)
    if target == "*":
        return normalized == ""
    return normalized.casefold() == target.casefold()
