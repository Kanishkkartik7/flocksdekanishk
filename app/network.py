"""Rebuild the network hierarchy from per-meter paths, and report what's inconsistent. Pure functions, no I/O.

The portal's hierarchy is messy (see PROTOCOL.md): some codes are blank, one transformer has two names, and codes
appear under several parents (e.g. D-01 under three circles). Nodes are therefore keyed by their full path, so the
tree always reflects exactly what each meter reports, and every anomaly is listed in `issues` instead of being
silently "fixed". The reasoning is in the README, under "Design decisions and trade-offs".
"""

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.models import NETWORK_LEVELS, DataIssue, Meter, NetworkNode, NetworkResponse, NetworkTreeNode, Transformer

MAX_LISTED_METERS = 25


@dataclass
class _Builder:
    level: str
    code: str | None
    names: Counter = field(default_factory=Counter)
    meter_count: int = 0
    children: dict[tuple, "_Builder"] = field(default_factory=dict)


def build_network(meters: Iterable[Meter], transformers: dict[str, Transformer]) -> NetworkResponse:
    meters = list(meters)
    root = _Builder(level="root", code=None)
    for meter in meters:
        node = root
        node.meter_count += 1
        for level in NETWORK_LEVELS:
            part: NetworkNode = getattr(meter.network, level)
            key = (part.code, None) if part.code else (None, part.name)  # blank code: group by name instead
            child = node.children.get(key)
            if child is None:
                child = node.children[key] = _Builder(level=level, code=part.code)
            if part.name:
                child.names[part.name] += 1
            child.meter_count += 1
            node = child

    zones = [_to_tree(child, transformers) for child in _sorted(root.children.values())]
    return NetworkResponse(levels=list(NETWORK_LEVELS), zones=zones, issues=find_issues(meters, transformers))


def find_issues(meters: list[Meter], transformers: dict[str, Transformer]) -> list[DataIssue]:
    issues: list[DataIssue] = []

    for level in NETWORK_LEVELS:
        blank_code = [m.meter_id for m in meters if getattr(m.network, level).code is None]
        if blank_code:
            issues.append(_issue("blank_code", level, None, f"{len(blank_code)} meter(s) have no {level} code", blank_code))

        names_by_code: dict[str, Counter] = defaultdict(Counter)
        blank_name: dict[str, list[str]] = defaultdict(list)
        for m in meters:
            part = getattr(m.network, level)
            if part.code is None:
                continue
            if part.name:
                names_by_code[part.code][part.name] += 1
            else:
                blank_name[part.code].append(m.meter_id)
        for code, ids in sorted(blank_name.items()):
            issues.append(_issue("blank_name", level, code, f"{len(ids)} meter(s) report {level} {code} with no name", ids))
        for code, names in sorted(names_by_code.items()):
            if len(names) > 1:
                listed = ", ".join(f"'{n}' ({c})" for n, c in names.most_common())
                issues.append(_issue("conflicting_names", level, code, f"{level} {code} has several names: {listed}"))

    for parent_level, level in zip(NETWORK_LEVELS, NETWORK_LEVELS[1:]):
        parents: dict[str, Counter] = defaultdict(Counter)
        for m in meters:
            child, parent = getattr(m.network, level), getattr(m.network, parent_level)
            if child.code and parent.code:  # blanks are already reported above
                parents[child.code][parent.code] += 1
        for code, found in sorted(parents.items()):
            if len(found) > 1:
                listed = ", ".join(f"{p} ({c} meters)" for p, c in sorted(found.items()))
                issues.append(
                    _issue("multiple_parents", level, code, f"{level} {code} appears under several {parent_level}s: {listed}")
                )

    feeders_by_dt: dict[str, Counter] = defaultdict(Counter)
    for m in meters:
        if m.network.dt.code and m.network.feeder.code:
            feeders_by_dt[m.network.dt.code][m.network.feeder.code] += 1
    for code, feeders in sorted(feeders_by_dt.items()):
        transformer = transformers.get(code)
        if transformer is None:
            issues.append(_issue("unknown_transformer", "dt", code, f"Meters reference {code}, which is not in the transformer list"))
        elif transformer.feeder_code and set(feeders) != {transformer.feeder_code}:
            issues.append(
                _issue(
                    "feeder_mismatch",
                    "dt",
                    code,
                    f"Transformer list says {code} is on feeder {transformer.feeder_code}; "
                    f"its meters report {', '.join(sorted(feeders))}",
                )
            )
    return issues


def _issue(kind: str, level: str, code: str | None, message: str, meter_ids: list[str] | None = None) -> DataIssue:
    ids = sorted(meter_ids or [])
    return DataIssue(kind=kind, level=level, code=code, message=message, meter_ids=ids[:MAX_LISTED_METERS])


def _sorted(builders: Iterable[_Builder]) -> list[_Builder]:
    return sorted(builders, key=lambda b: (b.code is None, b.code or "", _best_name(b) or ""))


def _best_name(builder: _Builder) -> str | None:
    return builder.names.most_common(1)[0][0] if builder.names else None


def _to_tree(builder: _Builder, transformers: dict[str, Transformer]) -> NetworkTreeNode:
    capacity = None
    if builder.level == "dt" and builder.code in transformers:
        capacity = transformers[builder.code].capacity_kva
    return NetworkTreeNode(
        level=builder.level,
        code=builder.code,
        name=_best_name(builder),
        meter_count=builder.meter_count,
        capacity_kva=capacity,
        children=[_to_tree(child, transformers) for child in _sorted(builder.children.values())],
    )
