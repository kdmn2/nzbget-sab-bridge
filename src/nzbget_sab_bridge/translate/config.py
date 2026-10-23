"""Minimal, read-only NZBGet ``config`` response synthesis.

Only category names are exposed. Which additional config options the app
requires is deliberately an assumption to validate in the bridge smoke test;
capture did not record option names or values.
"""

from __future__ import annotations

from .status import normalize_category


def build_config(categories: list[str]) -> list[dict[str, str]]:
    real_names: list[str] = []
    seen: set[str] = set()
    for category in categories:
        if category == "*":
            continue
        normalized = normalize_category(category)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        real_names.append(normalized)

    has_real_default = any(name.casefold() == "default" for name in real_names)
    names: list[str] = []
    if "*" in categories:
        # Use an unambiguous default label when a real "Default" category
        # exists. "*" is also accepted by the category resolver.
        names.append("Default" if not has_real_default else "*")
    names.extend(real_names)

    return [
        {"Name": f"Category{index}.Name", "Value": name}
        for index, name in enumerate(names, start=1)
    ]
