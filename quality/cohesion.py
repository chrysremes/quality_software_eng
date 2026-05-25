"""Cohesion metrics: LCOM (Lack of Cohesion in Methods) and Cyclomatic Complexity."""

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class ClassCohesionResult:
    """LCOM result for a single class."""

    class_name: str
    file_path: str
    method_count: int
    instance_var_count: int
    lcom: float  # 0.0 (perfect cohesion) to 1.0 (no cohesion)


@dataclass
class FunctionComplexity:
    """Cyclomatic complexity for a single function/method."""

    name: str
    file_path: str
    line: int
    complexity: int


@dataclass
class CohesionReport:
    """Aggregated cohesion metrics across the project."""

    lcom_results: List[ClassCohesionResult] = field(default_factory=list)
    complexity_results: List[FunctionComplexity] = field(default_factory=list)

    @property
    def avg_lcom(self) -> float:
        if not self.lcom_results:
            return 0.0
        return sum(r.lcom for r in self.lcom_results) / len(self.lcom_results)

    @property
    def avg_complexity(self) -> float:
        if not self.complexity_results:
            return 0.0
        return sum(r.complexity for r in self.complexity_results) / len(
            self.complexity_results
        )

    @property
    def max_complexity(self) -> int:
        if not self.complexity_results:
            return 0
        return max(r.complexity for r in self.complexity_results)

    @property
    def complexity_violations(self) -> List[FunctionComplexity]:
        """Functions with cyclomatic complexity > 10."""
        return [r for r in self.complexity_results if r.complexity > 10]

    @property
    def critical_complexity_violations(self) -> List[FunctionComplexity]:
        """Functions with cyclomatic complexity > 50."""
        return [r for r in self.complexity_results if r.complexity > 50]


# ---------------------------------------------------------------------------
# LCOM (Lack of Cohesion in Methods)
# ---------------------------------------------------------------------------
# LCOM = 1 - (sum of methods accessing each attribute / (methods * attributes))
# Uses LCOM4-like heuristic via AST: for each class, find which methods
# reference which instance variables (self.x).


class _InstanceVarVisitor(ast.NodeVisitor):
    """Collect instance variable accesses per method in a class."""

    def __init__(self) -> None:
        self.methods: Dict[str, Set[str]] = {}
        self._current_method: Optional[str] = None

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._current_method = node.name
        self.methods[node.name] = set()
        self.generic_visit(node)
        self._current_method = None

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (
            self._current_method
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ):
            self.methods[self._current_method].add(node.attr)
        self.generic_visit(node)


def compute_lcom(class_node: ast.ClassDef, file_path: str) -> ClassCohesionResult:
    """Compute LCOM for a single class AST node.

    LCOM = 1 - (sum_of_method_attribute_accesses) / (M * A)
    where M = number of methods, A = number of distinct instance attributes.
    Result clamped to [0, 1]. Lower is better (more cohesive).
    """
    visitor = _InstanceVarVisitor()
    visitor.visit(class_node)

    methods = {
        name: attrs
        for name, attrs in visitor.methods.items()
        if not name.startswith("__") or name in ("__init__", "__post_init__")
    }

    all_attrs: Set[str] = set()
    for attrs in methods.values():
        all_attrs |= attrs

    m = len(methods)
    a = len(all_attrs)

    if m == 0 or a == 0:
        lcom = 0.0  # trivial class
    else:
        total_accesses = sum(len(attrs & all_attrs) for attrs in methods.values())
        lcom = 1.0 - (total_accesses / (m * a))
        lcom = max(0.0, min(1.0, lcom))

    return ClassCohesionResult(
        class_name=class_node.name,
        file_path=file_path,
        method_count=m,
        instance_var_count=a,
        lcom=round(lcom, 4),
    )


# ---------------------------------------------------------------------------
# Cyclomatic Complexity
# ---------------------------------------------------------------------------
# CC = 1 + number of decision points (if, elif, for, while, except,
# with, assert, boolean ops and/or, ternary).


class _ComplexityVisitor(ast.NodeVisitor):
    """Count decision points inside a function body."""

    def __init__(self) -> None:
        self.complexity = 1  # baseline path

    def visit_If(self, node: ast.If) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        # Each 'and'/'or' adds a branch
        self.complexity += len(node.values) - 1
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self.complexity += 1
        self.complexity += len(node.ifs)
        self.generic_visit(node)


def compute_cyclomatic_complexity(
    func_node: ast.FunctionDef, file_path: str
) -> FunctionComplexity:
    """Compute cyclomatic complexity for a function AST node."""
    visitor = _ComplexityVisitor()
    visitor.visit(func_node)
    return FunctionComplexity(
        name=func_node.name,
        file_path=file_path,
        line=func_node.lineno,
        complexity=visitor.complexity,
    )


# ---------------------------------------------------------------------------
# File / project-level analysis
# ---------------------------------------------------------------------------


def analyze_file_cohesion(file_path: Path) -> Tuple[List[ClassCohesionResult], List[FunctionComplexity]]:
    """Analyze a single Python file for LCOM and cyclomatic complexity."""
    source = file_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return [], []

    lcom_results: List[ClassCohesionResult] = []
    cc_results: List[FunctionComplexity] = []
    path_str = str(file_path)

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            lcom_results.append(compute_lcom(node, path_str))
            # Also measure complexity of methods inside classes
            for item in ast.walk(node):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    cc_results.append(
                        compute_cyclomatic_complexity(item, path_str)
                    )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Top-level functions (not inside a class already counted)
            # Check parent by seeing if it's a direct child of Module
            pass

    # Collect top-level functions explicitly
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            cc_results.append(compute_cyclomatic_complexity(node, path_str))

    return lcom_results, cc_results


def analyze_cohesion(source_files: List[Path]) -> CohesionReport:
    """Analyze cohesion metrics across all source files."""
    report = CohesionReport()
    for fp in source_files:
        lcom, cc = analyze_file_cohesion(fp)
        report.lcom_results.extend(lcom)
        report.complexity_results.extend(cc)
    return report
