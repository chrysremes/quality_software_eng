"""Coupling metrics: Afferent (CA), Efferent (CE), Abstractness (A),
Instability (I), and Distance from Main Sequence (D)."""

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class ModuleCoupling:
    """Coupling metrics for a single logical module (package)."""

    module_name: str
    ca: int = 0  # afferent coupling – who depends on me
    ce: int = 0  # efferent coupling – who I depend on
    abstract_count: int = 0  # abstract classes / ABCs / protocols
    concrete_count: int = 0  # concrete classes

    @property
    def abstractness(self) -> float:
        total = self.abstract_count + self.concrete_count
        return self.abstract_count / total if total else 0.0

    @property
    def instability(self) -> float:
        total = self.ca + self.ce
        return self.ce / total if total else 0.0

    @property
    def distance_from_main_sequence(self) -> float:
        """D = |A + I - 1|. 0 is ideal; near 1 is zone of pain/uselessness."""
        return abs(self.abstractness + self.instability - 1.0)

    @property
    def zone(self) -> str:
        a, i = self.abstractness, self.instability
        if a > 0.7 and i < 0.3:
            return "zone_of_uselessness"
        if a < 0.3 and i > 0.7:
            return "zone_of_pain"
        return "balanced"


@dataclass
class CouplingReport:
    """Aggregated coupling metrics for the project."""

    modules: Dict[str, ModuleCoupling] = field(default_factory=dict)

    @property
    def avg_instability(self) -> float:
        if not self.modules:
            return 0.0
        return sum(m.instability for m in self.modules.values()) / len(self.modules)

    @property
    def avg_abstractness(self) -> float:
        if not self.modules:
            return 0.0
        return sum(m.abstractness for m in self.modules.values()) / len(self.modules)

    @property
    def avg_distance(self) -> float:
        if not self.modules:
            return 0.0
        return sum(m.distance_from_main_sequence for m in self.modules.values()) / len(
            self.modules
        )

    @property
    def pain_zone_modules(self) -> List[str]:
        return [n for n, m in self.modules.items() if m.zone == "zone_of_pain"]

    @property
    def uselessness_zone_modules(self) -> List[str]:
        return [n for n, m in self.modules.items() if m.zone == "zone_of_uselessness"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# The logical modules we track in this project
LOGICAL_MODULES = {
    "shared": "shared",
    "database_api.app": "database_api/app",
    "model_inference.app": "model_inference/app",
    "model_training.utils": "model_training/utils",
}


def _resolve_module(file_path: Path, project_root: Path) -> Optional[str]:
    """Map a file path to its logical module name."""
    try:
        rel = file_path.resolve().relative_to(project_root.resolve())
    except ValueError:
        return None
    rel_str = str(rel)
    for mod_name, mod_path in LOGICAL_MODULES.items():
        if rel_str.startswith(mod_path):
            return mod_name
    return None


def _extract_imports(tree: ast.AST) -> Set[str]:
    """Extract top-level module names from import statements."""
    imports: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split(".")[0])
    return imports


def _count_classes(tree: ast.AST) -> Tuple[int, int]:
    """Count abstract and concrete classes.

    A class is considered abstract if it inherits from ABC / Protocol
    or contains any method decorated with @abstractmethod.
    """
    abstract = 0
    concrete = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        is_abstract = False
        # Check bases for ABC / Protocol / ABCMeta
        for base in node.bases:
            base_name = ""
            if isinstance(base, ast.Name):
                base_name = base.id
            elif isinstance(base, ast.Attribute):
                base_name = base.attr
            if base_name in ("ABC", "Protocol", "ABCMeta"):
                is_abstract = True
        # Check for @abstractmethod decorators on methods
        for item in ast.walk(node):
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in item.decorator_list:
                    dec_name = ""
                    if isinstance(dec, ast.Name):
                        dec_name = dec.id
                    elif isinstance(dec, ast.Attribute):
                        dec_name = dec.attr
                    if dec_name == "abstractmethod":
                        is_abstract = True
        if is_abstract:
            abstract += 1
        else:
            concrete += 1
    return abstract, concrete


# Map import names to our logical module names
_IMPORT_TO_MODULE = {
    "shared": "shared",
    "database_api": "database_api.app",
    "model_inference": "model_inference.app",
    "model_training": "model_training.utils",
    # Intra-package imports that reference app submodules
    "app": None,  # resolved contextually
    "database_utils": "database_api.app",
    "init_database": "database_api.app",
    "load_env_configs": "database_api.app",
    "api_service": None,  # could be either service
    "task_manager": None,
    "worker": None,
    "model_inference_utils": "model_inference.app",
    "model_training_utils": "model_training.utils",
}


def analyze_coupling(
    source_files: List[Path], project_root: Path
) -> CouplingReport:
    """Analyze coupling metrics across logical modules."""
    report = CouplingReport()

    # Initialize modules
    for mod_name in LOGICAL_MODULES:
        report.modules[mod_name] = ModuleCoupling(module_name=mod_name)

    # Per-file analysis
    file_modules: Dict[str, str] = {}  # file_path -> module_name
    file_trees: Dict[str, ast.AST] = {}
    file_imports: Dict[str, Set[str]] = {}  # file_path -> set of import names

    for fp in source_files:
        mod = _resolve_module(fp, project_root)
        if mod is None:
            continue
        source = fp.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(fp))
        except SyntaxError:
            continue

        path_str = str(fp)
        file_modules[path_str] = mod
        file_trees[path_str] = tree
        file_imports[path_str] = _extract_imports(tree)

        # Count abstract/concrete classes
        a, c = _count_classes(tree)
        report.modules[mod].abstract_count += a
        report.modules[mod].concrete_count += c

    # Compute CA/CE from import relationships between modules
    # Build module-level dependency set
    module_deps: Dict[str, Set[str]] = {m: set() for m in LOGICAL_MODULES}

    for path_str, imports in file_imports.items():
        src_mod = file_modules[path_str]
        for imp in imports:
            # Direct match
            if imp in LOGICAL_MODULES:
                target = imp
            elif imp in _IMPORT_TO_MODULE and _IMPORT_TO_MODULE[imp]:
                target = _IMPORT_TO_MODULE[imp]
            else:
                continue
            if target != src_mod:
                module_deps[src_mod].add(target)

    # CE = number of modules I depend on
    for mod_name, deps in module_deps.items():
        report.modules[mod_name].ce = len(deps)

    # CA = number of modules that depend on me
    for mod_name in LOGICAL_MODULES:
        ca = sum(1 for deps in module_deps.values() if mod_name in deps)
        report.modules[mod_name].ca = ca

    return report
