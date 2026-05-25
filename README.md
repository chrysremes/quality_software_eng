# Quality Software Engineering

A comprehensive code quality evaluation pipeline that measures software engineering and architecture metrics. This tool computes multiple dimensions of code quality and combines them into a single fitness score suitable for CI/CD integration.

## Overview

This tool analyzes Python codebases across seven critical dimensions:

| Metric | Description | Weight |
|--------|-------------|--------|
| **Cohesion** | Lack of Cohesion in Methods (LCOM) and Cyclomatic Complexity | 1.5 |
| **Coupling** | Afferent/Efferent coupling, abstractness, instability, and distance from main sequence | 1.5 |
| **Test Coverage** | Test file existence, test-to-source ratio, assertion density, function coverage | 1.5 |
| **Documentation** | Docstring and type annotation coverage at module, class, and function levels | 1.0 |
| **Connascence** | Detection of implicit dependencies and problematic patterns (magic strings, type issues, positional parameters) | 1.0 |
| **Logical Components** | Analysis of module organization and component structure | 1.5 |
| **Performance** | Performance-related metrics and patterns | 2.0 |

Each dimension is normalized to [0, 1] (where 1 is best), then combined using weighted averages to produce a final **fitness score** in the range [0, 10].

## Installation

### Prerequisites
- Python 3.8+

### Setup

1. Clone the repository:
```bash
git clone <repository-url>
cd quality_software_eng
```

2. Install dependencies (if any):
```bash
pip install -r requirements.txt  # if requirements.txt exists
```

## Quick Start

### Basic Usage

Run the analysis on your codebase:

```bash
python -m quality
```

This will:
- Analyze all configured source directories
- Compute quality metrics across all dimensions
- Display a formatted report in the terminal
- Exit with code 0 if fitness score ≥ 6.0 (threshold), 1 if below threshold, 2 if error

### Generate JSON Output

For CI/CD integration, output results as JSON:

```bash
python -m quality --json
```

Example output:
```json
{
  "total_score": 7.45,
  "max_possible": 10,
  "grade": "B",
  "threshold": 6.0,
  "passed": true,
  "dimensions": { ... }
}
```

### Custom Quality Threshold

Set a custom fitness score threshold:

```bash
python -m quality --threshold 7.5
```

Exit codes:
- `0` — Fitness score ≥ threshold ✓
- `1` — Fitness score < threshold ✗
- `2` — Analysis error

### Save Report to File

Generate and save a formatted report:

```bash
python -m quality --json --output report.json
python -m quality --output report.txt
```

## Metrics Explained

### 1. Cohesion
Measures how closely related methods and variables are within a class.
- **LCOM (Lack of Cohesion in Methods)**: Ranges from 0.0 (perfect cohesion) to 1.0 (no cohesion)
- **Cyclomatic Complexity**: Tracks the complexity of individual functions/methods
- Lower values indicate better cohesion

### 2. Coupling
Analyzes dependencies between modules:
- **Afferent Coupling (CA)**: Number of modules that depend on this module
- **Efferent Coupling (CE)**: Number of modules this module depends on
- **Abstractness (A)**: Ratio of abstract classes/protocols to total classes
- **Instability (I)**: Ratio of CE / (CA + CE)
- **Distance from Main Sequence (D)**: How far a module is from ideal balance

### 3. Test Coverage
Evaluates testing quality:
- **Test-to-Source Ratio**: Lines of test code vs lines of source code
- **Test Function Coverage**: How many source functions have corresponding tests
- **Assertion Density**: Number and concentration of assertions in tests
- **Test File Existence**: Presence of dedicated test files per module

### 4. Documentation Quality
Measures code documentation and type hints:
- **Docstring Coverage**: Percentage of classes and functions with docstrings
- **Type Annotation Coverage**: Percentage of function parameters and return types with annotations
- **Module Documentation**: Presence of module-level docstrings

### 5. Connascence
Detects implicit dependencies and problematic patterns:
- **CoN (Name)**: Magic strings shared across modules
- **CoT (Type)**: Functions with missing type annotations
- **CoP (Position)**: Functions with too many positional parameters (>4)
- **CoV (Value)**: Magic numbers in logic
- **CoM (Meaning)**: Boolean parameters that hide intent
- **CoE (Execution Order)**: Global mutable state patterns

Lower connascence indicates better design.

### 6. Logical Components
Analyzes module organization and component boundaries:
- Structural integrity of logical components
- Component dependency analysis
- Namespace organization

### 7. Performance
Identifies performance-related patterns:
- Inefficient constructs
- Memory usage patterns
- Algorithm complexity issues

## Configuration

The tool analyzes directories specified in `quality/run_evaluation.py`:

```python
SOURCE_DIRS = [
    "shared",
    "database_api/app",
    "database_api/main.py",
    "model_inference/app",
    "model_inference/main.py",
    "model_training/utils",
]

EXCLUDE_PATTERNS = {"__pycache__", ".pyc", "test_", "conftest"}
```

Modify these to analyze your specific codebase.

## Command Reference

| Command | Description |
|---------|-------------|
| `python -m quality` | Run analysis with default settings |
| `python -m quality --json` | Output JSON format |
| `python -m quality --threshold 7.5` | Set custom threshold |
| `python -m quality --output report.json` | Save to file |
| `python -m quality --json --threshold 7.0 --output build/report.json` | Combined options |

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | ✓ Fitness score meets or exceeds threshold |
| 1 | ✗ Fitness score below threshold (quality gate failed) |
| 2 | ✗ Analysis error (file read error, etc.) |

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Code Quality Check

on: [push, pull_request]

jobs:
  quality:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      - run: pip install -r requirements.txt
      - run: python -m quality --threshold 6.0
        continue-on-error: false
```

### Local Pre-commit Hook

Create `.git/hooks/pre-commit`:

```bash
#!/bin/bash
python -m quality --threshold 6.0
if [ $? -ne 0 ]; then
    echo "Code quality check failed"
    exit 1
fi
```

## Output Formats

### Text Report (Default)

```
========================================================================
  CODE QUALITY EVALUATION REPORT
========================================================================
  Files analyzed : 42
  Analysis time  : 1.23s

  Cohesion              [#########-----------] 4.50 x1.5 = 6.75
    LCOM average: 0.35
    Max complexity: 18

  Coupling              [##########----------] 5.20 x1.5 = 7.80
    CA: 5, CE: 3, Instability: 0.375

  ...
------------------------------------------------------------------------
  FITNESS SCORE: 7.45 / 10   Grade: B
------------------------------------------------------------------------
```

### JSON Report

```json
{
  "total_score": 7.45,
  "max_possible": 10,
  "grade": "B",
  "threshold": 6.0,
  "passed": true,
  "analysis_time_seconds": 1.23,
  "files_analyzed": 42,
  "dimensions": {
    "cohesion": {
      "raw_score": 4.5,
      "weight": 1.5,
      "weighted_score": 6.75,
      "details": {...}
    },
    ...
  }
}
```

## Development

### Running Individual Analyzers

Each metric can be used independently:

```python
from quality.cohesion import analyze_cohesion
from quality.coupling import analyze_coupling
from quality.test_coverage import analyze_test_coverage

cohesion_report = analyze_cohesion(source_files)
coupling_report = analyze_coupling(source_files)
coverage_report = analyze_test_coverage(source_files)
```

### Module Reference

- `cohesion.py` — LCOM and cyclomatic complexity analysis
- `coupling.py` — Module coupling and architectural metrics
- `test_coverage.py` — Test coverage evaluation
- `doc_quality.py` — Documentation and type annotation metrics
- `connascence.py` — Connascence pattern detection
- `logical_components.py` — Logical component analysis
- `performance.py` — Performance pattern detection
- `fitness.py` — Fitness score computation and weighting
- `run_evaluation.py` — Main entry point and report formatting

## License

See [LICENSE](LICENSE) file for details.
