"""Documentation and typing quality metrics.

Measures:
- Docstring coverage (module, class, function/method levels)
- Type annotation coverage (argument and return types)
- Overall documentation quality score
"""

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class DocQualityFile:
    file_path: str
    total_functions: int = 0
    documented_functions: int = 0
    total_classes: int = 0
    documented_classes: int = 0
    has_module_docstring: bool = False
    total_params: int = 0
    annotated_params: int = 0
    total_returns: int = 0
    annotated_returns: int = 0


@dataclass
class QualityReport:
    file_metrics: List[DocQualityFile] = field(default_factory=list)

    @property
    def docstring_coverage(self) -> float:
        """Fraction of functions+classes that have docstrings."""
        total = sum(f.total_functions + f.total_classes for f in self.file_metrics)
        documented = sum(
            f.documented_functions + f.documented_classes for f in self.file_metrics
        )
        return documented / total if total else 1.0

    @property
    def module_docstring_coverage(self) -> float:
        """Fraction of files that have a module-level docstring."""
        if not self.file_metrics:
            return 1.0
        return sum(1 for f in self.file_metrics if f.has_module_docstring) / len(
            self.file_metrics
        )

    @property
    def type_annotation_coverage(self) -> float:
        """Fraction of parameters + returns that have type annotations."""
        total = sum(f.total_params + f.total_returns for f in self.file_metrics)
        annotated = sum(
            f.annotated_params + f.annotated_returns for f in self.file_metrics
        )
        return annotated / total if total else 1.0

    @property
    def overall_score(self) -> float:
        """Weighted quality score [0, 1]. Higher is better."""
        return (
            0.4 * self.docstring_coverage
            + 0.2 * self.module_docstring_coverage
            + 0.4 * self.type_annotation_coverage
        )


def _has_docstring(node: ast.AST) -> bool:
    """Check if a function/class/module node has a docstring."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
        if node.body and isinstance(node.body[0], ast.Expr):
            if isinstance(node.body[0].value, ast.Constant) and isinstance(
                node.body[0].value.value, str
            ):
                return True
    return False


def analyze_file_quality(file_path: Path) -> DocQualityFile:
    source = file_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return DocQualityFile(file_path=str(file_path))

    result = DocQualityFile(file_path=str(file_path))
    result.has_module_docstring = _has_docstring(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            result.total_classes += 1
            if _has_docstring(node):
                result.documented_classes += 1

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result.total_functions += 1
            if _has_docstring(node):
                result.documented_functions += 1

            # Count annotation coverage
            for arg in node.args.args:
                if arg.arg in ("self", "cls"):
                    continue
                result.total_params += 1
                if arg.annotation is not None:
                    result.annotated_params += 1

            # Return annotation (skip __init__)
            if node.name != "__init__":
                result.total_returns += 1
                if node.returns is not None:
                    result.annotated_returns += 1

    return result


def analyze_quality(source_files: List[Path]) -> QualityReport:
    """Analyze documentation and typing quality across all source files."""
    report = QualityReport()
    for fp in source_files:
        report.file_metrics.append(analyze_file_quality(fp))
    return report
