# Pandas: Import/File Tracking Bypass Patterns

## Executive Summary

**Total bypass patterns found: ~65**
- Library code: ~18
- Test code: ~25
- Benchmarks: ~7

**Most critical for test tracking:**
1. `mmap.mmap()` in CSV reading - Core library feature
2. `sys.modules` manipulation - 8 test occurrences for mocking
3. `subprocess` calls - Tests running code in isolation
4. `ctypes` on Windows - Clipboard operations

---

## 1. Memory-Mapped File Access (2 occurrences)

### 1.1 Library Code (CSV Optimization)

**File:** `pandas/io/common.py:1195-1199`

```python
wrapped = _IOWrapper(
    mmap.mmap(
        handle.fileno(),
        0,
        access=mmap.ACCESS_READ,
    )
)
```

**Context:** Called from `_get_filepath_or_buffer()` when `memory_map=True`

**Impact:** Any `pd.read_csv(..., memory_map=True)` call bypasses `builtins.open()` tracking after the initial file handle is created.

### 1.2 Test Code

**File:** `pandas/tests/io/parser/test_c_parser_only.py:560`

```python
def test_file_handles_mmap(all_parsers, csv1):
    with open(csv1, encoding="utf-8") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
            parser.read_csv(m)
```

---

## 2. Subprocess Execution (23 occurrences)

### 2.1 Clipboard Operations (Library - 12 occurrences)

**File:** `pandas/io/clipboard/__init__.py`

| Line | Code | Platform |
|------|------|----------|
| 102-104 | `subprocess.Popen(['pbcopy'], ...)` | macOS copy |
| 108-110 | `subprocess.Popen(['pbpaste'], ...)` | macOS paste |
| 174-177 | `subprocess.Popen(['xclip', '-selection', ...])` | Linux xclip copy |
| 183-188 | `subprocess.Popen(['xclip', '-selection', '-o', ...])` | Linux xclip paste |
| 205-208 | `subprocess.Popen(['xsel', '--clipboard', ...])` | Linux xsel copy |
| 214-217 | `subprocess.Popen(['xsel', '--clipboard', '-o'])` | Linux xsel paste |
| 233 | `subprocess.check_call(['wl-copy'], ...)` | Wayland copy |
| 235 | `subprocess.Popen(['wl-paste', '-n'], ...)` | Wayland paste |
| 252-262 | `subprocess.Popen(['qdbus', ...])` | KDE Klipper copy |
| 266-271 | `subprocess.Popen(['qdbus', ...])` | KDE Klipper paste |
| 508-509 | `subprocess.Popen(['clip.exe'], ...)` | WSL copy |
| 512-517 | `subprocess.Popen(['powershell.exe', '-command', 'Get-Clipboard'])` | WSL paste |

### 2.2 Test Code Subprocess Usage

| File | Line | Code | Purpose |
|------|------|------|---------|
| `tests/test_common.py` | 272 | `subprocess.check_output(call)` | Memory size measurement |
| `tests/test_downstream.py` | 116 | `subprocess.check_call([sys.executable, "-OO", "-c", "import pandas"])` | Python optimization test |
| `tests/test_downstream.py` | 122-131 | `subprocess.check_call([sys.executable, "-OO", "-c", "...pickle..."])` | Pickle in optimized mode |
| `tests/io/test_compression.py` | 233 | `subprocess.check_output([sys.executable, "-c", code])` | Missing lzma test |
| `tests/io/test_compression.py` | 252 | `subprocess.check_output([sys.executable, "-c", code])` | Runtime lzma test |
| `tests/plotting/test_converter.py` | 57 | `subprocess.check_output(call)` | Converter registration |
| `tests/plotting/test_converter.py` | 75 | `subprocess.check_call(call)` | Converter verification |
| `tests/tslibs/test_timezones.py` | 38 | `subprocess.check_call([sys.executable, "-c", code])` | Timezone handling |

---

## 3. sys.modules Manipulation (8 occurrences)

### Test Code - Module Injection

| File | Line | Code | Purpose |
|------|------|------|---------|
| `tests/test_optional_dependency.py` | 34 | `sys.modules[name] = module` | Inject fake module for version test |
| `tests/test_optional_dependency.py` | 73 | `sys.modules[name] = module` | Inject parent module |
| `tests/test_optional_dependency.py` | 77 | `sys.modules[f"{name}.{sub_name}"] = submodule` | Inject submodule |
| `tests/test_optional_dependency.py` | 96 | `sys.modules[name] = module` | Module without version |
| `tests/io/test_compression.py` | 229 | `sys.modules['lzma'] = None` | Block lzma import |
| `tests/io/test_compression.py` | 245 | `sys.modules['lzma'] = None` | Block lzma runtime |
| `tests/tslibs/test_timezones.py` | 30 | `sys.modules['tzdata'] = None` | Block tzdata |

---

## 4. ctypes Dynamic Library Loading (Windows Clipboard)

**File:** `pandas/io/clipboard/__init__.py:339-502`

```python
def init_windows_clipboard():
    # Line 356-357
    windll = ctypes.windll
    msvcrt = ctypes.CDLL("msvcrt")

    # Various Win32 API bindings:
    # Line 359: windll.user32.CreateWindowExA
    # Line 376: windll.user32.DestroyWindow
    # Line 380: windll.user32.OpenClipboard
    # Line 384: windll.user32.CloseClipboard
    # Line 388: windll.user32.EmptyClipboard
    # Line 392: windll.user32.GetClipboardData
    # Line 396: windll.user32.SetClipboardData
```

**Impact:** Windows clipboard operations use direct Win32 API calls through ctypes, completely bypassing Python file tracking.

---

## 5. SQLite Database Access (7 occurrences)

### Benchmark Code

| File | Line | Code |
|------|------|------|
| `asv_bench/benchmarks/io/sql.py` | 23 | `sqlite3.connect(":memory:")` |
| `asv_bench/benchmarks/io/sql.py` | 72 | `sqlite3.connect(":memory:")` |

### Test Code

| File | Line | Code |
|------|------|------|
| `tests/io/test_sql.py` | 876 | `sqlite3.connect(":memory:")` |
| `tests/io/test_sql.py` | 2584 | `sqlite3.connect(temp_file)` |
| `tests/io/test_sql.py` | 2587 | `sqlite3.connect(temp_file)` |
| `tests/io/test_sql.py` | 3883 | `sqlite3.connect(":memory:", detect_types=...)` |
| `tests/io/test_sql.py` | 4286 | `sqlite3.connect(":memory:")` |

**Note:** In-memory databases (`:memory:`) don't involve file I/O, but file-based connections do bypass tracking.

---

## 6. Entry Point Plugin System

**File:** `pandas/plotting/_core.py:2160-2226`

```python
def _load_backend(backend: str):
    from importlib.metadata import entry_points

    eps = entry_points()
    key = "pandas_plotting_backends"

    if hasattr(eps, "select"):
        entry = eps.select(group=key)
    else:
        entry = eps.get(key, ())

    for entry_point in entry:
        found_backend = entry_point.name == backend
        if found_backend:
            module = entry_point.load()  # Dynamic loading
            break

    if not found_backend:
        module = importlib.import_module(backend)  # Fallback
```

**Impact:** Plotting backends can be loaded via entry points, which uses metadata-based discovery rather than direct imports.

---

## 7. __import__ Mocking

**File:** `pandas/tests/test_downstream.py:196-207`

```python
def test_missing_required_dependency(monkeypatch, dependency):
    original_import = __import__
    mock_error = ImportError(f"Mock error for {dependency}")

    def mock_import(name, *args, **kwargs):
        if name == dependency:
            raise mock_error
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", mock_import)
    importlib.reload(importlib.import_module("pandas"))
```

**Impact:** This test directly replaces `builtins.__import__`, which would interfere with any import tracking that also hooks `__import__`.

---

## 8. NumPy File I/O (2 occurrences in tests)

| File | Line | Code |
|------|------|------|
| `tests/series/methods/test_to_csv.py` | 76 | `np.loadtxt(temp_file)` |
| `tests/reshape/test_qcut.py` | 101 | `np.loadtxt(cut_file)` |

---

## Detection Script

```python
#!/usr/bin/env python3
"""
Detect pandas test modules using bypass patterns.
Run from pandas repository root.
"""

import ast
import sys
from pathlib import Path

PANDAS_ROOT = Path("pandas")

class BypassPatternVisitor(ast.NodeVisitor):
    def __init__(self, filepath):
        self.filepath = filepath
        self.findings = []

    def visit_Call(self, node):
        # subprocess calls
        if isinstance(node.func, ast.Attribute):
            if node.func.attr in ('run', 'Popen', 'check_output', 'check_call', 'call'):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == 'subprocess':
                    self.findings.append({
                        'pattern': f'subprocess.{node.func.attr}',
                        'line': node.lineno,
                        'file': str(self.filepath)
                    })
            # mmap.mmap
            elif node.func.attr == 'mmap':
                if isinstance(node.func.value, ast.Name) and node.func.value.id == 'mmap':
                    self.findings.append({
                        'pattern': 'mmap.mmap',
                        'line': node.lineno,
                        'file': str(self.filepath)
                    })
            # sqlite3.connect
            elif node.func.attr == 'connect':
                if isinstance(node.func.value, ast.Name) and node.func.value.id == 'sqlite3':
                    self.findings.append({
                        'pattern': 'sqlite3.connect',
                        'line': node.lineno,
                        'file': str(self.filepath)
                    })
            # numpy file I/O
            elif node.func.attr in ('load', 'fromfile', 'memmap', 'loadtxt'):
                if isinstance(node.func.value, ast.Name) and node.func.value.id in ('np', 'numpy'):
                    self.findings.append({
                        'pattern': f'numpy.{node.func.attr}',
                        'line': node.lineno,
                        'file': str(self.filepath)
                    })
            # ctypes.CDLL
            elif node.func.attr == 'CDLL':
                self.findings.append({
                    'pattern': 'ctypes.CDLL',
                    'line': node.lineno,
                    'file': str(self.filepath)
                })
            # entry_point.load()
            elif node.func.attr == 'load':
                self.findings.append({
                    'pattern': 'entry_point.load (potential)',
                    'line': node.lineno,
                    'file': str(self.filepath)
                })

        self.generic_visit(node)

    def visit_Subscript(self, node):
        # sys.modules[...] = ...
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
    test_dir = PANDAS_ROOT / "tests"
    all_findings = []

    for pyfile in test_dir.rglob("*.py"):
        findings = scan_file(pyfile)
        all_findings.extend(findings)

    # Also scan library code
    for pyfile in PANDAS_ROOT.rglob("*.py"):
        if "tests" not in str(pyfile):
            findings = scan_file(pyfile)
            for f in findings:
                f['type'] = 'library'
            all_findings.extend(findings)

    # Group by file
    by_file = {}
    for f in all_findings:
        by_file.setdefault(f['file'], []).append(f)

    print("=" * 70)
    print("PANDAS FILES WITH BYPASS PATTERNS")
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

### High Priority (Multiple Bypass Patterns)

| File | Patterns |
|------|----------|
| `tests/test_optional_dependency.py` | sys.modules (4x) |
| `tests/test_downstream.py` | subprocess (2x), __import__ mock |
| `tests/io/test_compression.py` | subprocess (2x), sys.modules (2x) |
| `tests/io/test_sql.py` | sqlite3.connect (5x) |
| `tests/tslibs/test_timezones.py` | subprocess, sys.modules |
| `tests/plotting/test_converter.py` | subprocess (2x) |

### Library Files with Bypass Patterns

| File | Pattern | Impact |
|------|---------|--------|
| `pandas/io/common.py:1195` | mmap.mmap | CSV memory mapping |
| `pandas/io/clipboard/__init__.py` | subprocess (12x), ctypes | All clipboard ops |
| `pandas/plotting/_core.py:2175` | entry_points | Plugin backends |
| `pandas/compat/_optional.py:158` | importlib.import_module | Optional deps |

---

## Key Observations

1. **Clipboard is heavily subprocess-dependent**: 12 subprocess calls for cross-platform clipboard support
2. **Windows uses ctypes extensively**: Direct Win32 API access for clipboard
3. **Memory mapping in CSV**: Core feature that bypasses open() tracking
4. **Test isolation via sys.modules**: Common pattern for testing optional dependencies
5. **No exec() calls**: Unlike matplotlib/scipy, pandas doesn't use exec() in library code
