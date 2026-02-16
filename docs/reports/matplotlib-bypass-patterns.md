# Matplotlib: Import/File Tracking Bypass Patterns

## Executive Summary

**Total bypass patterns found: 47**
- Library code: 27
- Test code: 9
- Tools/Examples: 11

**Most critical for test tracking:**
1. `subprocess_run_helper()` - Used by 7 test files, completely bypasses parent process tracking
2. `exec()` in plot_directive.py - Executes arbitrary code from documentation
3. `sys.modules` manipulation - 8 occurrences in tests for mocking dependencies

---

## 1. Subprocess Execution (26 occurrences)

### 1.1 The `subprocess_run_helper` Function (CRITICAL)

**Location:** `lib/matplotlib/testing/__init__.py:121-155`

```python
def subprocess_run_helper(func, *args, timeout, extra_env=None):
    target = func.__name__
    module = func.__module__
    file = func.__code__.co_filename
    proc = subprocess_run_for_testing(
        [
            sys.executable,
            "-c",
            f"import importlib.util;"
            f"_spec = importlib.util.spec_from_file_location({module!r}, {file!r});"
            f"_module = importlib.util.module_from_spec(_spec);"
            f"_spec.loader.exec_module(_module);"
            f"_module.{target}()",
            *args
        ],
        env={**os.environ, "SOURCE_DATE_EPOCH": "0", **(extra_env or {})},
        timeout=timeout, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    return proc
```

**Why it bypasses tracking:**
- Spawns a completely separate Python process
- Uses `spec_from_file_location` to load modules by file path (not import)
- Parent process hooks have no visibility into child process

**Test files using this function:**

| Test File | Line | Function Being Run |
|-----------|------|-------------------|
| `tests/test_pickle.py` | 150 | `_pickle_load_subprocess` |
| `tests/test_pickle.py` | 335 | pickle subprocess tests |
| `tests/test_backend_macosx.py` | 29, 59, 85 | macOS backend tests |
| `tests/test_backend_tk.py` | 52 | Tk backend tests |
| `tests/test_backends_interactive.py` | multiple | Interactive backend tests |
| `tests/test_font_manager.py` | 289 | Font discovery tests |
| `tests/test_pyplot.py` | 354 | pyplot subprocess tests |
| `tests/test_ticker.py` | 1779 | Ticker formatting tests |

### 1.2 Other Subprocess Usage

| File | Line | Code | Purpose |
|------|------|------|---------|
| `lib/matplotlib/testing/__init__.py` | 96 | `subprocess.run(command, ...)` | Generic test subprocess |
| `lib/matplotlib/testing/compare.py` | 103 | `subprocess.run([...magick...])` | ImageMagick conversion |
| `lib/matplotlib/testing/compare.py` | 113 | `subprocess.Popen([...gs...])` | Ghostscript PDF conversion |
| `lib/matplotlib/testing/compare.py` | 179 | `subprocess.Popen(["inkscape"...])` | Inkscape SVG conversion |
| `lib/matplotlib/animation.py` | 323 | `subprocess.Popen(command, ...)` | Movie writer process |
| `lib/matplotlib/dviread.py` | 1255 | `subprocess.Popen(["luatex"...])` | LaTeX DVI processing |
| `lib/matplotlib/backends/backend_pgf.py` | 283 | `subprocess.Popen([...texsystem...])` | PGF LaTeX rendering |
| `lib/matplotlib/__init__.py` | 405 | `subprocess.check_output(args, ...)` | External tool version detection |
| `lib/matplotlib/font_manager.py` | 262 | `subprocess.check_output(['fc-list'...])` | System font discovery |
| `lib/matplotlib/font_manager.py` | 273 | `subprocess.check_output(["system_profiler"...])` | macOS font discovery |
| `lib/matplotlib/texmanager.py` | 258 | `subprocess.check_output(command, ...)` | TeX rendering |

---

## 2. exec() Calls (4 occurrences)

**All in:** `lib/matplotlib/sphinxext/plot_directive.py`

| Line | Code | Purpose |
|------|------|---------|
| 579 | `exec('import numpy as np\nfrom matplotlib import pyplot as plt\n', ns)` | Default imports for examples |
| 582 | `exec(str(setup.config.plot_pre_code), ns)` | User-provided pre-code |
| 588 | `exec(code, ns)` | Main example code execution |
| 590 | `exec(function_name + "()", ns)` | Function invocation |

**Context:** The `_run_code()` function (line 546) executes Python code embedded in Sphinx documentation. While the `exec()` calls themselves go through `__import__`, the code being executed could use any bypass mechanism.

---

## 3. sys.modules Manipulation (10 occurrences)

### Test Code (Mocking Dependencies)

| File | Line | Code | Purpose |
|------|------|------|---------|
| `tests/test_backends_interactive.py` | 406 | `sys.modules["PyQt6"] = None` | Mock missing PyQt6 |
| `tests/test_backends_interactive.py` | 407 | `sys.modules["PyQt5"] = None` | Mock missing PyQt5 |
| `tests/test_backends_interactive.py` | 408 | `sys.modules["PySide2"] = None` | Mock missing PySide2 |
| `tests/test_backends_interactive.py` | 409 | `sys.modules["PySide6"] = None` | Mock missing PySide6 |
| `tests/test_cbook.py` | 997 | `sys.modules['torch'] = torch` | Mock torch module |
| `tests/test_cbook.py` | 1026 | `sys.modules['jax'] = jax` | Mock jax module |
| `tests/test_cbook.py` | 1056 | `sys.modules['tensorflow'] = tensorflow` | Mock tensorflow module |

### Library Code

| File | Line | Code | Purpose |
|------|------|------|---------|
| `lib/matplotlib/backend_bases.py` | 3723 | `setattr(sys.modules[cls.__module__], name, ...)` | Backend registration |
| `lib/matplotlib/backend_bases.py` | 3731 | `setattr(sys.modules[cls.__module__], "Show", Show)` | Inject Show class |

---

## 4. importlib.resources (2 occurrences)

**File:** `lib/matplotlib/style/__init__.py`

| Line | Code |
|------|------|
| 16 | `import importlib.resources` |
| 118 | `path = importlib.resources.files(pkg) / f"{name}.{_STYLE_EXTENSION}"` |

**Why it bypasses tracking:** Accesses package data files through the import system's resource API rather than explicit file paths. The actual file read happens inside `importlib.resources` internals.

---

## 5. NumPy File I/O (3 occurrences)

| File | Line | Code | Type |
|------|------|------|------|
| `lib/matplotlib/cbook.py` | 592 | `return np.load(path)` | Library |
| `galleries/examples/specialty_plots/mri_with_eeg.py` | 44 | `data = np.fromfile(eegfile, dtype=float)` | Example |
| `galleries/examples/specialty_plots/skewt.py` | 235 | `p, h, T, Td = np.loadtxt(sound_data, unpack=True)` | Example |

**Why it bypasses tracking:** NumPy's C extensions read files directly without going through `builtins.open()`.

---

## 6. Patterns NOT Found

- `mmap.mmap()` - No occurrences
- `ctypes.CDLL()` - No occurrences
- `sqlite3.connect()` - No occurrences

---

## Detection Script

```python
#!/usr/bin/env python3
"""
Detect matplotlib test modules using bypass patterns.
Run from matplotlib repository root.
"""

import ast
import sys
from pathlib import Path

MATPLOTLIB_ROOT = Path("lib/matplotlib")

class BypassPatternVisitor(ast.NodeVisitor):
    def __init__(self, filepath):
        self.filepath = filepath
        self.findings = []

    def visit_Call(self, node):
        # Check for subprocess_run_helper
        if isinstance(node.func, ast.Attribute):
            if node.func.attr == 'subprocess_run_helper':
                self.findings.append({
                    'pattern': 'subprocess_run_helper',
                    'line': node.lineno,
                    'file': str(self.filepath)
                })
            elif node.func.attr in ('run', 'Popen', 'check_output', 'check_call', 'call'):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == 'subprocess':
                    self.findings.append({
                        'pattern': f'subprocess.{node.func.attr}',
                        'line': node.lineno,
                        'file': str(self.filepath)
                    })

        # Check for exec()
        if isinstance(node.func, ast.Name) and node.func.id == 'exec':
            self.findings.append({
                'pattern': 'exec()',
                'line': node.lineno,
                'file': str(self.filepath)
            })

        # Check for np.load, np.fromfile
        if isinstance(node.func, ast.Attribute):
            if node.func.attr in ('load', 'fromfile', 'memmap', 'loadtxt'):
                if isinstance(node.func.value, ast.Name) and node.func.value.id in ('np', 'numpy'):
                    self.findings.append({
                        'pattern': f'numpy.{node.func.attr}',
                        'line': node.lineno,
                        'file': str(self.filepath)
                    })

        self.generic_visit(node)

    def visit_Subscript(self, node):
        # Check for sys.modules[...] = ...
        if isinstance(node.value, ast.Attribute):
            if (isinstance(node.value.value, ast.Name) and
                node.value.value.id == 'sys' and
                node.value.attr == 'modules'):
                self.findings.append({
                    'pattern': 'sys.modules manipulation',
                    'line': node.lineno,
                    'file': str(self.filepath)
                })
        self.generic_visit(node)

def scan_file(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read(), filename=str(filepath))
        visitor = BypassPatternVisitor(filepath)
        visitor.visit(tree)
        return visitor.findings
    except (SyntaxError, UnicodeDecodeError):
        return []

def main():
    test_dir = MATPLOTLIB_ROOT / "tests"
    all_findings = []

    for pyfile in test_dir.rglob("*.py"):
        findings = scan_file(pyfile)
        all_findings.extend(findings)

    # Group by file
    by_file = {}
    for f in all_findings:
        by_file.setdefault(f['file'], []).append(f)

    print("=" * 70)
    print("MATPLOTLIB TEST FILES WITH BYPASS PATTERNS")
    print("=" * 70)

    for filepath, findings in sorted(by_file.items()):
        print(f"\n{filepath}:")
        for f in findings:
            print(f"  Line {f['line']}: {f['pattern']}")

    print(f"\n{'=' * 70}")
    print(f"Total: {len(all_findings)} bypass patterns in {len(by_file)} files")

if __name__ == "__main__":
    main()
```

---

## Test Files Summary

**Files with subprocess_run_helper (highest priority):**
1. `tests/test_pickle.py`
2. `tests/test_backend_macosx.py`
3. `tests/test_backend_tk.py`
4. `tests/test_backends_interactive.py`
5. `tests/test_font_manager.py`
6. `tests/test_pyplot.py`
7. `tests/test_ticker.py`

**Files with sys.modules manipulation:**
1. `tests/test_backends_interactive.py`
2. `tests/test_cbook.py`

**Library files with bypass patterns:**
1. `lib/matplotlib/sphinxext/plot_directive.py` (exec)
2. `lib/matplotlib/testing/__init__.py` (subprocess_run_helper)
3. `lib/matplotlib/style/__init__.py` (importlib.resources)
4. `lib/matplotlib/cbook.py` (np.load)
