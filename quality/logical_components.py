"""Logical components analysis: role clarity, Law of Demeter violations,
and static/dynamic coupling assessment."""

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .coupling import LOGICAL_MODULES, _resolve_module


@dataclass
class DemeterViolation:
    """A Law of Demeter violation (chained attribute access a.b.c)."""

    file_path: str
    line: int
    chain: str
    depth: int


@dataclass
class ModuleRole:
    """Assessed role and responsibility for a logical module."""

    module_name: str
    primary_role: str  # e.g. "API layer", "data access", "shared infra"
    has_mixed_responsibilities: bool
    responsibility_tags: List[str]  # e.g. ["routing", "db_access", "validation"]


@dataclass
class LogicalComponentsReport:
    module_roles: List[ModuleRole] = field(default_factory=list)
    demeter_violations: List[DemeterViolation] = field(default_factory=list)
    temporal_coupling_risks: List[str] = field(default_factory=list)

    @property
    def demeter_violation_count(self) -> int:
        return len(self.demeter_violations)

    @property
    def mixed_responsibility_count(self) -> int:
        return sum(1 for r in self.module_roles if r.has_mixed_responsibilities)


# ---------------------------------------------------------------------------
# Role detection heuristics
# ---------------------------------------------------------------------------

# Tags derived from content patterns
_ROLE_PATTERNS = {
    "routing": {
        "fastapi",
        "apirouter",
        "router",
        "endpoint",
        "uploadfile",
        "httpexception",
        "depends",
    },
    "db_access": {
        "engine",
        "session",
        "query",
        "insert",
        "sqlalchemy",
        "table_name",
        "database_url",
        "create_engine",
        "read_sql",
        "to_sql",
    },
    "validation": {"validate", "schema", "check", "pydantic"},
    "business_logic": {"predict", "train", "evaluate", "pipeline", "model"},
    "infrastructure": {"redis", "task", "worker", "queue", "poll"},
    "configuration": {"config", "env", "settings", "load_env"},
    "serialization": {"json", "serialize", "parse", "encode", "decode"},
}

_CROSS_CUTTING_TAGS = {"serialization"}

# Known module role assignments
_MODULE_EXPECTED_ROLES: Dict[str, Tuple[str, Set[str]]] = {
    "shared": (
        "shared infrastructure",
        {
            "infrastructure",
            "configuration",
            "db_access",
            "business_logic",
            "routing",
        },
    ),
    "database_api.app": ("data ingestion API", {"routing", "db_access", "infrastructure", "validation"}),
    "model_inference.app": ("inference API", {"routing", "business_logic", "infrastructure", "validation"}),
    "model_training.utils": ("model training utilities", {"business_logic", "db_access", "configuration"}),
}


def _detect_tags_in_file(tree: ast.AST) -> Set[str]:
    """Detect responsibility tags from identifiers in an AST."""
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            names.add(node.attr.lower())
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name.lower())
        elif isinstance(node, ast.ClassDef):
            names.add(node.name.lower())

    tags: Set[str] = set()
    for tag, keywords in _ROLE_PATTERNS.items():
        if names & keywords:
            tags.add(tag)
    return tags


def _assess_module_role(
    module_name: str, file_tags: Dict[str, Set[str]]
) -> ModuleRole:
    """Assess a module's role clarity."""
    all_tags: Set[str] = set()
    for tags in file_tags.values():
        all_tags |= tags

    expected_role, expected_tags = _MODULE_EXPECTED_ROLES.get(
        module_name, ("unknown", set())
    )

    # A module has mixed responsibilities if it has tags far outside its expected set
    unexpected = (all_tags - expected_tags) - _CROSS_CUTTING_TAGS
    # Allow one unexpected tag before flagging
    has_mixed = len(unexpected) > 1

    return ModuleRole(
        module_name=module_name,
        primary_role=expected_role,
        has_mixed_responsibilities=has_mixed,
        responsibility_tags=sorted(all_tags),
    )


# ---------------------------------------------------------------------------
# Law of Demeter
# ---------------------------------------------------------------------------

DEMETER_DEPTH_THRESHOLD = 2  # a.b is OK, a.b.c is a violation


def _measure_chain_depth(node: ast.Attribute) -> Tuple[int, str]:
    """Walk an attribute chain and return (depth, chain_string)."""
    parts = [node.attr]
    current = node.value
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    parts.reverse()
    return len(parts) - 1, ".".join(parts)


def _detect_demeter_violations(
    tree: ast.AST, file_path: str
) -> List[DemeterViolation]:
    """Find chained attribute accesses that exceed the Demeter threshold."""
    violations: List[DemeterViolation] = []
    seen_lines: Set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            depth, chain = _measure_chain_depth(node)
            if depth > DEMETER_DEPTH_THRESHOLD and node.lineno not in seen_lines:
                # Ignore common safe chains (e.g., self.x.y, os.path.join)
                if chain.startswith("self.") or chain.startswith("os.") or chain.startswith("np."):
                    continue
                violations.append(
                    DemeterViolation(
                        file_path=file_path,
                        line=node.lineno,
                        chain=chain,
                        depth=depth,
                    )
                )
                seen_lines.add(node.lineno)
    return violations


# ---------------------------------------------------------------------------
# Temporal coupling detection
# ---------------------------------------------------------------------------


def _detect_temporal_coupling(
    source_files: List[Path], project_root: Path
) -> List[str]:
    """Detect patterns indicating temporal coupling between modules.

    Heuristic: if a module both reads global state AND calls functions
    that mutate global state in another module, there's temporal coupling risk.
    """
    risks: List[str] = []
    global_writers: Dict[str, Set[str]] = {}  # module -> set of global names written
    global_readers: Dict[str, Set[str]] = {}  # module -> set of global names read

    for fp in source_files:
        mod = _resolve_module(fp, project_root)
        if mod is None:
            continue
        source = fp.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(fp))
        except SyntaxError:
            continue

        writers = global_writers.setdefault(mod, set())
        readers = global_readers.setdefault(mod, set())

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and not target.id.isupper():
                        writers.add(target.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for stmt in ast.walk(node):
                    if isinstance(stmt, ast.Global):
                        for name in stmt.names:
                            writers.add(name)

    # Cross-module temporal coupling
    for mod_a, writes in global_writers.items():
        for mod_b, reads in global_readers.items():
            if mod_a != mod_b:
                shared = writes & reads
                if shared:
                    risks.append(
                        f"{mod_a} writes globals used by {mod_b}: {shared}"
                    )

    return risks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_logical_components(
    source_files: List[Path], project_root: Path
) -> LogicalComponentsReport:
    """Analyze logical component quality."""
    report = LogicalComponentsReport()

    # Per-module file tags
    module_file_tags: Dict[str, Dict[str, Set[str]]] = {
        m: {} for m in LOGICAL_MODULES
    }

    for fp in source_files:
        mod = _resolve_module(fp, project_root)
        if mod is None:
            continue
        source = fp.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(fp))
        except SyntaxError:
            continue

        tags = _detect_tags_in_file(tree)
        module_file_tags[mod][str(fp)] = tags

        # Demeter violations
        report.demeter_violations.extend(_detect_demeter_violations(tree, str(fp)))

    # Assess roles
    for mod_name, file_tags in module_file_tags.items():
        report.module_roles.append(_assess_module_role(mod_name, file_tags))

    # Temporal coupling
    report.temporal_coupling_risks = _detect_temporal_coupling(
        source_files, project_root
    )

    return report
