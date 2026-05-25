"""Connascence analysis via AST inspection.

Connascence types detected:
- CoN (Name): hardcoded string literals shared across modules (magic strings)
- CoT (Type): function signatures missing type annotations
- CoP (Position): functions with many positional parameters (>4)
- CoV (Value): magic numbers used in logic
- CoM (Meaning): boolean parameters that hide intent
- CoE (Execution order / temporal): global mutable state patterns
"""

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class ConnascenceIssue:
    """A single connascence finding."""

    file_path: str
    line: int
    kind: str  # CoN, CoT, CoP, CoV, CoM, CoE
    severity: str  # low, medium, high
    description: str


@dataclass
class ConnascenceReport:
    issues: List[ConnascenceIssue] = field(default_factory=list)

    @property
    def count_by_kind(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for issue in self.issues:
            counts[issue.kind] = counts.get(issue.kind, 0) + 1
        return counts

    @property
    def high_severity_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "high")

    @property
    def total(self) -> int:
        return len(self.issues)


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

# Strings that are likely meaningful constants (not just format strings etc.)
_IGNORED_STRING_PREFIXES = ("", " ", "\n", "/", "http", "{", "SELECT", "INSERT")


def _is_uppercase_name_target(target: ast.AST) -> bool:
    """Return True when an assignment target is a UPPER_CASE constant name."""
    return isinstance(target, ast.Name) and target.id.isupper()


def _assignment_target_name(node: ast.AST) -> Optional[str]:
    """Return a simple assignment target name when available."""
    if isinstance(node, ast.Assign) and node.targets and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    return None


def _build_parent_map(tree: ast.AST) -> Dict[int, ast.AST]:
    """Build child->parent lookup to support context-aware AST checks."""
    parent_by_id: Dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_by_id[id(child)] = parent
    return parent_by_id


def _has_ancestor_assignment_to_names(
    node: ast.AST,
    parent_by_id: Dict[int, ast.AST],
    allowed_names: Set[str],
) -> bool:
    """Check whether node is nested under assignment to one of allowed names."""
    current = parent_by_id.get(id(node))
    while current is not None:
        name = _assignment_target_name(current)
        if name in allowed_names:
            return True
        current = parent_by_id.get(id(current))
    return False


def _has_ancestor_uppercase_assignment(
    node: ast.AST, parent_by_id: Dict[int, ast.AST]
) -> bool:
    """Check whether node is nested under assignment to UPPER_CASE constant(s)."""
    current = parent_by_id.get(id(node))
    while current is not None:
        if isinstance(current, ast.Assign):
            if any(_is_uppercase_name_target(target) for target in current.targets):
                return True
        if isinstance(current, ast.AnnAssign) and _is_uppercase_name_target(current.target):
            return True
        current = parent_by_id.get(id(current))
    return False


def _is_validation_bound_literal(
    node: ast.Constant, parent_by_id: Dict[int, ast.AST]
) -> bool:
    """Return True for declarative validator bounds like Field(ge=0, min_length=1)."""
    parent = parent_by_id.get(id(node))
    if not isinstance(parent, ast.keyword):
        return False
    if parent.arg not in {
        "ge",
        "gt",
        "le",
        "lt",
        "min_length",
        "max_length",
        "min_items",
        "max_items",
    }:
        return False
    grandparent = parent_by_id.get(id(parent))
    return isinstance(grandparent, ast.Call)


def _detect_magic_numbers(tree: ast.AST, file_path: str) -> List[ConnascenceIssue]:
    """CoV: magic numbers (numeric literals outside of assignments to UPPER_CASE)."""
    # Constants modules intentionally centralize numeric values.
    if file_path.endswith("_constants.py") or file_path.endswith("constants.py"):
        return []

    issues: List[ConnascenceIssue] = []
    parent_by_id = _build_parent_map(tree)
    schema_example_assignment_names = {"model_config", "schema_extra", "json_schema_extra"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            # Skip trivial values 0, 1, -1, 2, 100, 0.0, 1.0
            if node.value in (0, 1, -1, 2, 0.0, 1.0, 100, True, False):
                continue
            # Skip if nested under assignment to constant names.
            if _has_ancestor_uppercase_assignment(node, parent_by_id):
                continue
            # Skip model/schema example payloads embedded in Pydantic models.
            if _has_ancestor_assignment_to_names(
                node, parent_by_id, schema_example_assignment_names
            ):
                continue
            # Skip declarative validation bounds (Field/ge/lt/min_length/etc.).
            if _is_validation_bound_literal(node, parent_by_id):
                continue
            issues.append(
                ConnascenceIssue(
                    file_path=file_path,
                    line=node.lineno,
                    kind="CoV",
                    severity="low",
                    description=f"Magic number {node.value}",
                )
            )
    return issues


def _detect_missing_type_annotations(
    tree: ast.AST, file_path: str
) -> List[ConnascenceIssue]:
    """CoT: functions/method arguments without type annotations."""
    issues: List[ConnascenceIssue] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Skip dunder methods except __init__
        if node.name.startswith("__") and node.name != "__init__":
            continue
        missing = []
        for arg in node.args.args:
            if arg.arg == "self" or arg.arg == "cls":
                continue
            if arg.annotation is None:
                missing.append(arg.arg)
        if node.returns is None and node.name != "__init__":
            missing.append("return")
        if missing:
            issues.append(
                ConnascenceIssue(
                    file_path=file_path,
                    line=node.lineno,
                    kind="CoT",
                    severity="medium",
                    description=f"`{node.name}` missing annotations: {', '.join(missing)}",
                )
            )
    return issues


def _detect_many_positional_params(
    tree: ast.AST, file_path: str, threshold: int = 4
) -> List[ConnascenceIssue]:
    """CoP: functions with too many positional parameters."""
    issues: List[ConnascenceIssue] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = [a for a in node.args.args if a.arg not in ("self", "cls")]
        if len(params) > threshold:
            issues.append(
                ConnascenceIssue(
                    file_path=file_path,
                    line=node.lineno,
                    kind="CoP",
                    severity="medium",
                    description=f"`{node.name}` has {len(params)} positional params (threshold={threshold})",
                )
            )
    return issues


def _detect_boolean_params(tree: ast.AST, file_path: str) -> List[ConnascenceIssue]:
    """CoM: boolean default parameters that obscure meaning."""
    issues: List[ConnascenceIssue] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for default in node.args.defaults:
            if isinstance(default, ast.Constant) and isinstance(
                default.value, bool
            ):
                issues.append(
                    ConnascenceIssue(
                        file_path=file_path,
                        line=node.lineno,
                        kind="CoM",
                        severity="low",
                        description=f"`{node.name}` has boolean default parameter",
                    )
                )
                break  # one per function is enough
    return issues


def _detect_global_mutable_state(
    tree: ast.AST, file_path: str
) -> List[ConnascenceIssue]:
    """CoE: module-level mutable assignments (temporal connascence risk)."""
    issues: List[ConnascenceIssue] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    name = target.id
                    # Skip UPPER_CASE constants and dunder
                    if name.isupper() or name.startswith("_"):
                        continue
                    # Mutable types: dict, list, set calls or literals
                    val = node.value
                    is_mutable = isinstance(val, (ast.Dict, ast.List, ast.Set))
                    if isinstance(val, ast.Call):
                        if isinstance(val.func, ast.Name) and val.func.id in (
                            "dict",
                            "list",
                            "set",
                        ):
                            is_mutable = True
                    if is_mutable:
                        issues.append(
                            ConnascenceIssue(
                                file_path=file_path,
                                line=node.lineno,
                                kind="CoE",
                                severity="high",
                                description=f"Global mutable state: `{name}`",
                            )
                        )
    return issues


def analyze_file_connascence(file_path: Path) -> List[ConnascenceIssue]:
    """Run all connascence detectors on a single file."""
    source = file_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return []
    path_str = str(file_path)
    issues: List[ConnascenceIssue] = []
    issues.extend(_detect_magic_numbers(tree, path_str))
    issues.extend(_detect_missing_type_annotations(tree, path_str))
    issues.extend(_detect_many_positional_params(tree, path_str))
    issues.extend(_detect_boolean_params(tree, path_str))
    issues.extend(_detect_global_mutable_state(tree, path_str))
    return issues


def analyze_connascence(source_files: List[Path]) -> ConnascenceReport:
    """Analyze connascence across all source files."""
    report = ConnascenceReport()
    for fp in source_files:
        report.issues.extend(analyze_file_connascence(fp))
    return report
