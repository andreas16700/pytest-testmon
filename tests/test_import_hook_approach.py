"""
Verify the zero-processing import hook approach.

Tests that capturing (name, result.__name__, fromlist) at import time,
with all processing deferred to reconciliation, correctly maps imports
to actual Python files.
"""

import builtins
import importlib
import os
import sys
import textwrap
import types
from collections import defaultdict

import pytest


# ---------------------------------------------------------------------------
# Minimal hook implementation (the approach under test)
# ---------------------------------------------------------------------------


class ImportRecorder:
    """Records raw import data with zero processing in the hook."""

    def __init__(self, project_root: str):
        self.project_root = os.path.realpath(project_root)
        self._root_prefix = self.project_root + os.sep
        self._original_import = None
        self._current: defaultdict[str, set] = defaultdict(set)
        self._resolve_cache: dict[str, str | None] = {}

    def install(self):
        self._original_import = builtins.__import__
        builtins.__import__ = self._hook

    def uninstall(self):
        if self._original_import is not None:
            builtins.__import__ = self._original_import
            self._original_import = None

    def _hook(self, name, globals=None, locals=None, fromlist=(), level=0):
        result = self._original_import(name, globals, locals, fromlist, level)
        # Normalize: CPython internally passes fromlist as list (unhashable).
        # tuple() on a tuple returns the same object, so no cost for the common case.
        fl = tuple(fromlist) if fromlist is not None else None
        self._current[name].add(fl)
        self._current[result.__name__].add(fl)
        return result

    def clear(self):
        self._current = defaultdict(set)

    def snapshot(self) -> dict[str, set]:
        return dict(self._current)

    # -- Reconciliation (all processing happens here) ----------------------

    def _resolve(self, mod_name: str) -> str | None:
        if mod_name in self._resolve_cache:
            return self._resolve_cache[mod_name]

        module = sys.modules.get(mod_name)
        if not module:
            self._resolve_cache[mod_name] = None
            return None

        spec = getattr(module, "__spec__", None)
        origin = getattr(spec, "origin", None) if spec else None
        path = origin or getattr(module, "__file__", None)

        if not path:
            self._resolve_cache[mod_name] = None
            return None

        realpath = os.path.realpath(path)
        if not realpath.startswith(self._root_prefix):
            self._resolve_cache[mod_name] = None
            return None

        relpath = os.path.relpath(realpath, self.project_root).replace(os.sep, "/")
        self._resolve_cache[mod_name] = relpath
        return relpath

    def reconcile(self, recorded: dict[str, set] | None = None) -> set[str]:
        if recorded is None:
            recorded = self._current

        files: set[str] = set()

        for key, fromlists in recorded.items():
            if not key:
                continue

            # 1. Prefix expansion
            parts = key.split(".")
            for i in range(len(parts)):
                f = self._resolve(".".join(parts[: i + 1]))
                if f:
                    files.add(f)

            # 2. Fromlist expansion
            for fl in fromlists:
                if fl is None:
                    continue
                for item in fl:
                    candidate = f"{key}.{item}"
                    f = self._resolve(candidate)
                    if f:
                        files.add(f)
                    else:
                        # Attribute — trace defining module
                        mod = sys.modules.get(key)
                        if mod:
                            attr = getattr(mod, item, None)
                            defining = getattr(attr, "__module__", None)
                            if defining:
                                f = self._resolve(defining)
                                if f:
                                    files.add(f)

        return files


# ---------------------------------------------------------------------------
# Fixture: temporary package on disk
# ---------------------------------------------------------------------------


@pytest.fixture
def pkg_dir(tmp_path):
    """Create a package structure:

    mypkg/
        __init__.py          (from .models import Product)
        models/
            __init__.py      (from .product import Product)
            product.py       (class Product)
            user.py          (class User)
        utils.py             (def helper())
        sub/
            __init__.py      (empty)
            deep.py          (def deep_fn())
    """
    root = tmp_path / "mypkg"
    (root / "models").mkdir(parents=True)
    (root / "sub").mkdir(parents=True)

    (root / "__init__.py").write_text(
        textwrap.dedent("""\
        from .models import Product
        """)
    )

    (root / "models" / "__init__.py").write_text(
        textwrap.dedent("""\
        from .product import Product
        from .user import User
        """)
    )

    (root / "models" / "product.py").write_text(
        textwrap.dedent("""\
        class Product:
            pass
        """)
    )

    (root / "models" / "user.py").write_text(
        textwrap.dedent("""\
        class User:
            pass
        """)
    )

    (root / "utils.py").write_text(
        textwrap.dedent("""\
        def helper():
            return 42
        """)
    )

    (root / "sub" / "__init__.py").write_text("")

    (root / "sub" / "deep.py").write_text(
        textwrap.dedent("""\
        def deep_fn():
            return 99
        """)
    )

    # --- Additional structures for edge case tests ---

    # Relative imports (Group 1)
    (root / "sibling_b.py").write_text(
        textwrap.dedent("""\
        def shared_fn():
            return "shared"
        """)
    )

    (root / "sibling_a.py").write_text(
        textwrap.dedent("""\
        from .sibling_b import shared_fn
        """)
    )

    # Star imports (Group 2)
    (root / "star_target.py").write_text(
        textwrap.dedent("""\
        __all__ = ["StarClass"]

        class StarClass:
            pass

        class _Private:
            pass
        """)
    )

    # Circular imports (Group 3)
    (root / "circular_a.py").write_text(
        textwrap.dedent("""\
        A_VAL = "a"
        from .circular_b import B_VAL
        """)
    )

    (root / "circular_b.py").write_text(
        textwrap.dedent("""\
        B_VAL = "b"
        from .circular_a import A_VAL
        """)
    )

    # multi_init package (Group 8)
    (root / "multi_init").mkdir()

    (root / "multi_init" / "__init__.py").write_text(
        textwrap.dedent("""\
        from .mod_a import A
        from .mod_b import B
        """)
    )

    (root / "multi_init" / "mod_a.py").write_text(
        textwrap.dedent("""\
        class A:
            pass
        """)
    )

    (root / "multi_init" / "mod_b.py").write_text(
        textwrap.dedent("""\
        class B:
            pass
        """)
    )

    # deep_reexport package (Group 10)
    (root / "deep_reexport").mkdir()

    (root / "deep_reexport" / "__init__.py").write_text(
        textwrap.dedent("""\
        from .layer1 import DeepClass
        """)
    )

    (root / "deep_reexport" / "layer1").mkdir()

    (root / "deep_reexport" / "layer1" / "__init__.py").write_text(
        textwrap.dedent("""\
        from .layer2 import DeepClass
        """)
    )

    (root / "deep_reexport" / "layer1" / "layer2.py").write_text(
        textwrap.dedent("""\
        class DeepClass:
            pass
        """)
    )

    # weird module for __module__ = None (Group 11)
    (root / "weird.py").write_text(
        textwrap.dedent("""\
        Obj = type("Obj", (), {"__module__": None})
        """)
    )

    # Namespace package (no __init__.py) (Group 6)
    ns = tmp_path / "nspkg"
    ns.mkdir()

    (ns / "leaf.py").write_text(
        textwrap.dedent("""\
        def ns_fn():
            return "namespace"
        """)
    )

    # Add tmp_path to sys.path so imports work
    sys.path.insert(0, str(tmp_path))
    yield tmp_path
    sys.path.remove(str(tmp_path))

    # Clean up sys.modules
    to_remove = [
        k for k in sys.modules
        if k == "mypkg" or k.startswith("mypkg.")
        or k == "nspkg" or k.startswith("nspkg.")
    ]
    for k in to_remove:
        del sys.modules[k]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestImportHookCapture:
    """Verify the hook captures the right raw data."""

    def test_plain_import(self, pkg_dir):
        recorder = ImportRecorder(str(pkg_dir))
        recorder.install()
        try:
            recorder.clear()
            import mypkg.utils  # noqa: F401

            data = recorder.snapshot()
            # name="mypkg.utils" should be a key
            assert "mypkg.utils" in data
            # result.__name__="mypkg" (top-level returned when no fromlist)
            assert "mypkg" in data
        finally:
            recorder.uninstall()

    def test_from_import_attribute(self, pkg_dir):
        recorder = ImportRecorder(str(pkg_dir))
        recorder.install()
        try:
            recorder.clear()
            from mypkg.models import Product  # noqa: F401

            data = recorder.snapshot()
            assert "mypkg.models" in data
            # fromlist should contain ("Product",)
            assert ("Product",) in data["mypkg.models"]
        finally:
            recorder.uninstall()

    def test_from_import_submodule(self, pkg_dir):
        recorder = ImportRecorder(str(pkg_dir))
        recorder.install()
        try:
            recorder.clear()
            from mypkg import utils  # noqa: F401

            data = recorder.snapshot()
            assert "mypkg" in data
            assert ("utils",) in data["mypkg"]
        finally:
            recorder.uninstall()

    def test_dotted_import(self, pkg_dir):
        recorder = ImportRecorder(str(pkg_dir))
        recorder.install()
        try:
            recorder.clear()
            import mypkg.sub.deep  # noqa: F401

            data = recorder.snapshot()
            # name="mypkg.sub.deep" captures the full chain
            assert "mypkg.sub.deep" in data
        finally:
            recorder.uninstall()


class TestReconciliation:
    """Verify reconciliation maps recorded data to actual files."""

    def test_plain_import_resolves_full_chain(self, pkg_dir):
        """import mypkg.utils → mypkg/__init__.py + mypkg/utils.py"""
        recorder = ImportRecorder(str(pkg_dir))
        recorder.install()
        try:
            recorder.clear()
            import mypkg.utils  # noqa: F401

            files = recorder.reconcile()
        finally:
            recorder.uninstall()

        assert "mypkg/__init__.py" in files
        assert "mypkg/utils.py" in files

    def test_from_import_submodule_via_fromlist(self, pkg_dir):
        """from mypkg import utils → resolves mypkg/__init__.py + mypkg/utils.py"""
        recorder = ImportRecorder(str(pkg_dir))
        recorder.install()
        try:
            recorder.clear()
            from mypkg import utils  # noqa: F401

            files = recorder.reconcile()
        finally:
            recorder.uninstall()

        assert "mypkg/__init__.py" in files
        assert "mypkg/utils.py" in files

    def test_from_import_reexported_attribute(self, pkg_dir):
        """from mypkg.models import Product → traces Product.__module__ to models/product.py"""
        recorder = ImportRecorder(str(pkg_dir))
        recorder.install()
        try:
            recorder.clear()
            from mypkg.models import Product  # noqa: F401

            files = recorder.reconcile()
        finally:
            recorder.uninstall()

        assert "mypkg/models/__init__.py" in files
        assert "mypkg/models/product.py" in files

    def test_dotted_import_resolves_all_intermediates(self, pkg_dir):
        """import mypkg.sub.deep → all three __init__.py files + deep.py"""
        recorder = ImportRecorder(str(pkg_dir))
        recorder.install()
        try:
            recorder.clear()
            import mypkg.sub.deep  # noqa: F401

            files = recorder.reconcile()
        finally:
            recorder.uninstall()

        assert "mypkg/__init__.py" in files
        assert "mypkg/sub/__init__.py" in files
        assert "mypkg/sub/deep.py" in files

    def test_from_deep_submodule_import(self, pkg_dir):
        """from mypkg.sub.deep import deep_fn → traces to deep.py"""
        recorder = ImportRecorder(str(pkg_dir))
        recorder.install()
        try:
            recorder.clear()
            from mypkg.sub.deep import deep_fn  # noqa: F401

            files = recorder.reconcile()
        finally:
            recorder.uninstall()

        assert "mypkg/sub/deep.py" in files

    def test_the_critical_case_from_pkg_import_class(self, pkg_dir):
        """The case that broke sys.modules diffing:
        from mypkg.models import Product where Product is re-exported
        from mypkg.models.product.

        Must resolve to BOTH models/__init__.py AND models/product.py.
        """
        recorder = ImportRecorder(str(pkg_dir))
        recorder.install()
        try:
            recorder.clear()
            from mypkg.models import Product  # noqa: F401

            files = recorder.reconcile()
        finally:
            recorder.uninstall()

        assert "mypkg/models/__init__.py" in files, "must capture the package init"
        assert "mypkg/models/product.py" in files, (
            "must trace Product.__module__ to the defining file"
        )

    def test_stdlib_not_included(self, pkg_dir):
        """stdlib imports should resolve to None (outside project root)."""
        recorder = ImportRecorder(str(pkg_dir))
        recorder.install()
        try:
            recorder.clear()
            import json  # noqa: F401
            import os.path  # noqa: F401

            files = recorder.reconcile()
        finally:
            recorder.uninstall()

        # No stdlib files should appear
        for f in files:
            assert not f.startswith(".."), f"stdlib file leaked: {f}"

    def test_already_loaded_module_still_captured(self, pkg_dir):
        """If a module is already in sys.modules, the hook still fires
        and the reconciliation still finds the file."""
        # Pre-load the module
        import mypkg.utils  # noqa: F401

        recorder = ImportRecorder(str(pkg_dir))
        recorder.install()
        try:
            recorder.clear()
            # Import again — module is already loaded
            import mypkg.utils  # noqa: F401

            files = recorder.reconcile()
        finally:
            recorder.uninstall()

        assert "mypkg/utils.py" in files


# ---------------------------------------------------------------------------
# Edge case tests (Groups 1–12)
# ---------------------------------------------------------------------------


class TestRelativeImports:
    """Group 1: Relative imports."""

    def test_relative_import_sibling(self, pkg_dir):
        """from mypkg.sibling_a import shared_fn triggers internal
        'from .sibling_b import shared_fn' → both files captured."""
        recorder = ImportRecorder(str(pkg_dir))
        recorder.install()
        try:
            recorder.clear()
            from mypkg.sibling_a import shared_fn  # noqa: F401

            files = recorder.reconcile()
        finally:
            recorder.uninstall()

        assert "mypkg/sibling_a.py" in files
        assert "mypkg/sibling_b.py" in files

    def test_relative_import_result_name(self, pkg_dir):
        """Verify result.__name__ gives absolute name for relative imports.
        The raw 'name' param may be just 'sibling_b' (garbage for resolution),
        but result.__name__ is 'mypkg.sibling_b' (correct)."""
        recorder = ImportRecorder(str(pkg_dir))
        recorder.install()
        try:
            recorder.clear()
            from mypkg.sibling_a import shared_fn  # noqa: F401

            data = recorder.snapshot()
            files = recorder.reconcile()
        finally:
            recorder.uninstall()

        # result.__name__ records the absolute name
        assert "mypkg.sibling_b" in data
        # Reconciliation correctly resolves via the absolute name
        assert "mypkg/sibling_b.py" in files


class TestStarImports:
    """Group 2: Star imports."""

    def test_star_import_captures_fromlist(self, pkg_dir):
        """from mypkg.star_target import * → hook records fromlist=('*',).
        Module itself captured via prefix expansion. Individual __all__
        members are NOT individually traced by the hook."""
        recorder = ImportRecorder(str(pkg_dir))
        recorder.install()
        try:
            recorder.clear()
            exec("from mypkg.star_target import *", {})

            data = recorder.snapshot()
            files = recorder.reconcile()
        finally:
            recorder.uninstall()

        # Verify fromlist contains ('*',)
        assert ("*",) in data.get("mypkg.star_target", set())
        # Module file captured via prefix expansion
        assert "mypkg/star_target.py" in files


class TestCircularImports:
    """Group 3: Circular imports."""

    def test_circular_import_no_crash(self, pkg_dir):
        """import mypkg.circular_a → no infinite loop.
        Both circular_a.py and circular_b.py captured."""
        recorder = ImportRecorder(str(pkg_dir))
        recorder.install()
        try:
            recorder.clear()
            import mypkg.circular_a  # noqa: F401

            files = recorder.reconcile()
        finally:
            recorder.uninstall()

        assert "mypkg/circular_a.py" in files
        assert "mypkg/circular_b.py" in files


class TestImportlibImportModule:
    """Group 4: importlib.import_module."""

    def test_importlib_import_module(self, pkg_dir):
        """importlib.import_module('mypkg.utils') — verify the module loads
        and sub-imports from module initialization are captured."""
        recorder = ImportRecorder(str(pkg_dir))
        recorder.install()
        try:
            recorder.clear()
            mod = importlib.import_module("mypkg.utils")

            data = recorder.snapshot()
            files = recorder.reconcile()
        finally:
            recorder.uninstall()

        assert mod.__name__ == "mypkg.utils"
        # importlib.import_module bypasses builtins.__import__ directly,
        # but loading mypkg triggers __init__.py imports via builtins.__import__
        assert "mypkg/__init__.py" in files

    def test_importlib_relative_import(self, pkg_dir):
        """importlib.import_module('.utils', package='mypkg')."""
        # Pre-load mypkg so the relative import can resolve the package
        import mypkg  # noqa: F401

        recorder = ImportRecorder(str(pkg_dir))
        recorder.install()
        try:
            recorder.clear()
            mod = importlib.import_module(".utils", package="mypkg")
        finally:
            recorder.uninstall()

        assert mod.__name__ == "mypkg.utils"


class TestFailedImports:
    """Group 5: Conditional / failed imports."""

    def test_failed_import_not_recorded(self, pkg_dir):
        """try: import mypkg.nonexistent; except ImportError: pass
        → nothing recorded for nonexistent."""
        recorder = ImportRecorder(str(pkg_dir))
        recorder.install()
        try:
            recorder.clear()
            try:
                import mypkg.nonexistent  # noqa: F401
            except ImportError:
                pass

            data = recorder.snapshot()
            files = recorder.reconcile()
        finally:
            recorder.uninstall()

        assert "mypkg.nonexistent" not in data
        for f in files:
            assert "nonexistent" not in f


class TestNamespacePackages:
    """Group 6: Namespace packages."""

    def test_namespace_package_submodule(self, pkg_dir):
        """import nspkg.leaf → leaf.py captured.
        Namespace package (no __init__.py) resolves to None."""
        recorder = ImportRecorder(str(pkg_dir))
        recorder.install()
        try:
            recorder.clear()
            import nspkg.leaf  # noqa: F401

            files = recorder.reconcile()
        finally:
            recorder.uninstall()

        assert "nspkg/leaf.py" in files
        assert "nspkg/__init__.py" not in files


class TestMultipleFromlistItems:
    """Group 7: Multiple fromlist items."""

    def test_multiple_fromlist_items(self, pkg_dir):
        """from mypkg.models import Product, User → both defining files resolved."""
        recorder = ImportRecorder(str(pkg_dir))
        recorder.install()
        try:
            recorder.clear()
            from mypkg.models import Product, User  # noqa: F401

            files = recorder.reconcile()
        finally:
            recorder.uninstall()

        assert "mypkg/models/product.py" in files
        assert "mypkg/models/user.py" in files


class TestMixedFromlist:
    """Group 8: Mixed fromlist (submodules + attributes)."""

    def test_fromlist_mix_submodule_and_attribute(self, pkg_dir):
        """from mypkg.multi_init import A, B → traces to defining modules."""
        recorder = ImportRecorder(str(pkg_dir))
        recorder.install()
        try:
            recorder.clear()
            from mypkg.multi_init import A, B  # noqa: F401

            files = recorder.reconcile()
        finally:
            recorder.uninstall()

        assert "mypkg/multi_init/__init__.py" in files
        assert "mypkg/multi_init/mod_a.py" in files
        assert "mypkg/multi_init/mod_b.py" in files


class TestSameModuleDifferentForms:
    """Group 9: Same module imported multiple ways."""

    def test_same_module_different_import_forms(self, pkg_dir):
        """Multiple import forms for the same module → deduplicated."""
        recorder = ImportRecorder(str(pkg_dir))
        recorder.install()
        try:
            recorder.clear()
            import mypkg.utils  # noqa: F401
            from mypkg import utils  # noqa: F401
            from mypkg.utils import helper  # noqa: F401

            files = recorder.reconcile()
        finally:
            recorder.uninstall()

        assert "mypkg/utils.py" in files
        # Set guarantees uniqueness
        assert len([f for f in files if f == "mypkg/utils.py"]) == 1


class TestDeepReexportChain:
    """Group 10: Deep re-export chains."""

    def test_deep_reexport_chain(self, pkg_dir):
        """from mypkg.deep_reexport import DeepClass → traces through chain."""
        recorder = ImportRecorder(str(pkg_dir))
        recorder.install()
        try:
            recorder.clear()
            from mypkg.deep_reexport import DeepClass  # noqa: F401

            files = recorder.reconcile()
        finally:
            recorder.uninstall()

        assert "mypkg/deep_reexport/__init__.py" in files
        assert "mypkg/deep_reexport/layer1/__init__.py" in files
        assert "mypkg/deep_reexport/layer1/layer2.py" in files


class TestModuleAttrNone:
    """Group 11: __module__ is None or unusual."""

    def test_module_attr_none(self, pkg_dir):
        """Object with __module__ = None → no crash, module file still captured."""
        recorder = ImportRecorder(str(pkg_dir))
        recorder.install()
        try:
            recorder.clear()
            from mypkg.weird import Obj  # noqa: F401

            files = recorder.reconcile()
        finally:
            recorder.uninstall()

        # Module file captured via prefix expansion
        assert "mypkg/weird.py" in files


class TestAlreadyLoadedWithNewFromlist:
    """Group 12: Already-loaded module with fromlist."""

    def test_already_loaded_with_new_fromlist(self, pkg_dir):
        """Pre-load mypkg.models, then from mypkg.models import User
        → User's defining file captured via __module__ trace."""
        # Pre-load
        import mypkg.models  # noqa: F401

        recorder = ImportRecorder(str(pkg_dir))
        recorder.install()
        try:
            recorder.clear()
            from mypkg.models import User  # noqa: F401

            files = recorder.reconcile()
        finally:
            recorder.uninstall()

        assert "mypkg/models/user.py" in files
