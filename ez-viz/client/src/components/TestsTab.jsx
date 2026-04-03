import { useState, useMemo } from "react";
import TestItem from "./TestItem.jsx";
import SearchBox from "./SearchBox.jsx";

function TestsTab({ pytestTests, search, setSearch, showTestDetails }) {
  const [sortByStatus, setSortByStatus] = useState(false);
  const [sortByRuntime, setSortByRuntime] = useState(false);

  const [statusFilter, setStatusFilter] = useState({
    passed: true,
    failed: true,
    skipped: true,
  });

  const toggleStatusFilter = (key) => {
    setStatusFilter((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const filterKey = (status) => {
    if (status === "error") return "failed";
    return status;
  };

  const getStatusOrder = (status) => {
    switch (status) {
      case "failed":  return 0;
      case "error":   return 0;
      case "skipped": return 1;
      case "passed":  return 2;
      default:        return 99;
    }
  };

  const tests = useMemo(() => {
    if (!pytestTests || pytestTests.length === 0) return [];

    let flat = pytestTests.flatMap(({ run_id, tests: runTests }) =>
      runTests.map((t) => ({
        name: t.nodeid,
        status: t.outcome,
        duration: t.duration || 0,
        lineno: t.lineno ?? null,
        error_message: t.error_message || null,
        longrepr: t.longrepr || null,
        run_id,
      }))
    );

    flat = flat.filter((t) => t.name.toLowerCase().includes(search.toLowerCase()));
    flat = flat.filter((t) => statusFilter[filterKey(t.status)]);

    if (sortByStatus && sortByRuntime) {
      flat = [...flat].sort((a, b) => {
        const d = getStatusOrder(a.status) - getStatusOrder(b.status);
        return d !== 0 ? d : (b.duration || 0) - (a.duration || 0);
      });
    } else if (sortByStatus) {
      flat = [...flat].sort((a, b) => getStatusOrder(a.status) - getStatusOrder(b.status));
    } else if (sortByRuntime) {
      flat = [...flat].sort((a, b) => (b.duration || 0) - (a.duration || 0));
    }

    return flat;
  }, [pytestTests, search, sortByStatus, sortByRuntime, statusFilter]);

  return (
    <div className="tests-tab-container">
      <div className="tests-search-wrapper">
        <SearchBox value={search} onChange={setSearch} placeholder="🔍 Search tests..." />
      </div>

      <div className="tests-controls-bar">
        <label className="sort-control-label">
          <input type="checkbox" checked={sortByStatus} onChange={() => setSortByStatus((p) => !p)} className="sort-checkbox-input" />
          <span className="sort-label-text">Order by status</span>
        </label>

        <label className="sort-control-label">
          <input type="checkbox" checked={sortByRuntime} onChange={() => setSortByRuntime((p) => !p)} className="sort-checkbox-input" />
          <span className="sort-label-text">Order by runtime</span>
        </label>

        <div className="status-filter-panel">
          <span className="filter-panel-title">Visible statuses</span>
          <div className="filter-badges-group">
            <label className="status-badge-filter status-badge-failed">
              <input type="checkbox" checked={statusFilter.failed} onChange={() => toggleStatusFilter("failed")} className="filter-checkbox-small" />
              <span className="font-medium">Failed</span>
            </label>

            <label className="status-badge-filter status-badge-skipped">
              <input type="checkbox" checked={statusFilter.skipped} onChange={() => toggleStatusFilter("skipped")} className="filter-checkbox-small" />
              <span className="font-medium">Skipped</span>
            </label>
            <label className="status-badge-filter status-badge-executed">
              <input type="checkbox" checked={statusFilter.passed} onChange={() => toggleStatusFilter("passed")} className="filter-checkbox-small" />
              <span className="font-medium">Passed</span>
            </label>
          </div>
        </div>
      </div>

      <div className="test-list-grid">
        {tests.map((test, idx) => (
          <TestItem
            key={`${test.run_id}-${idx}`}
            runId={test.run_id}
            test={test}
            onClick={() => showTestDetails(test)}
          />
        ))}
      </div>
    </div>
  );
}

export default TestsTab;
