import { useState, useMemo } from "react";
import TestItem from "./TestItem.jsx";
import SearchBox from "./SearchBox.jsx";

function TestsTab({ allTests, pytestTests, search, setSearch, showTestDetails }) {
  const [sortByStatus, setSortByStatus] = useState(false);
  const [sortByRuntime, setSortByRuntime] = useState(false);

  const [statusFilter, setStatusFilter] = useState({
    passed: true,
    failed: true,
    skipped: true,
    // ezmon-only fallback statuses
    executed: true,
    forced: true,
  });

  const toggleStatusFilter = (key) => {
    setStatusFilter((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  // deselected counts as skipped for filtering purposes
  const filterKey = (status) => status === "deselected" ? "skipped" : status;

  const getStatusOrder = (status) => {
    switch (status) {
      case "failed":     return 0;
      case "skipped":    return 1;
      case "deselected": return 1;
      case "passed":     return 2;
      case "executed":   return 2;
      case "forced":     return 3;
      default:           return 99;
    }
  };

  // Build a lookup from testmon tests by name -> test (for id, run_id)
  const testmonMap = useMemo(() => {
    const map = {};
    for (const runData of allTests) {
      for (const t of runData.tests) {
        map[t.name] = { ...t, run_id: runData.run_id };
      }
    }
    return map;
  }, [allTests]);

  // If pytest report is available, use it as primary source
  const tests = useMemo(() => {
    let flat;

    if (pytestTests && pytestTests.length > 0) {
      // pytestTests is [{ run_id, tests[] }] — flatten all runs
      flat = pytestTests.flatMap(({ run_id, tests: runTests }) =>
        runTests.map((t) => {
          const testmonEntry = testmonMap[t.nodeid];
          return {
            name: t.nodeid,
            status: t.outcome,
            duration: t.duration || 0,
            lineno: t.lineno ?? null,
            error_message: t.error_message || null,
            longrepr: t.longrepr || null,
            run_id: run_id,
            id: testmonEntry?.id ?? null,
          };
        })
      );
    } else {
      // Fallback to testmon data when no pytest report
      flat = allTests.flatMap((runData) =>
        runData.tests.map((t) => {
          let status;
          if (t.failed) status = "failed";
          else if (t.forced === 1) status = "forced";
          else if (t.forced === 0) status = "executed";
          else status = "skipped";
          return {
            name: t.name,
            status,
            duration: t.duration || 0,
            error_message: null,
            longrepr: null,
            id: t.id,
            run_id: runData.run_id,
          };
        })
      );
    }

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
  }, [pytestTests, allTests, testmonMap, search, sortByStatus, sortByRuntime, statusFilter]);

  const usingPytest = pytestTests && pytestTests.length > 0 && pytestTests[0].tests;

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

            {usingPytest ? (
              <>
                <label className="status-badge-filter status-badge-skipped">
                  <input type="checkbox" checked={statusFilter.skipped} onChange={() => toggleStatusFilter("skipped")} className="filter-checkbox-small" />
                  <span className="font-medium">Skipped</span>
                </label>
                <label className="status-badge-filter status-badge-executed">
                  <input type="checkbox" checked={statusFilter.passed} onChange={() => toggleStatusFilter("passed")} className="filter-checkbox-small" />
                  <span className="font-medium">Passed</span>
                </label>
              </>
            ) : (
              <>
                <label className="status-badge-filter status-badge-skipped">
                  <input type="checkbox" checked={statusFilter.skipped} onChange={() => toggleStatusFilter("skipped")} className="filter-checkbox-small" />
                  <span className="font-medium">Skipped</span>
                </label>
                <label className="status-badge-filter status-badge-executed">
                  <input type="checkbox" checked={statusFilter.executed} onChange={() => toggleStatusFilter("executed")} className="filter-checkbox-small" />
                  <span className="font-medium">Executed</span>
                </label>
                <label className="status-badge-filter status-badge-forced">
                  <input type="checkbox" checked={statusFilter.forced} onChange={() => toggleStatusFilter("forced")} className="filter-checkbox-small" />
                  <span className="font-medium">Forced</span>
                </label>
              </>
            )}
          </div>
        </div>
      </div>

      <div className="test-list-grid">
        {tests.map((test, idx) => (
          <TestItem
            key={test.id != null ? `${test.id}-${test.run_id}` : `test-${idx}`}
            runId={test.run_id}
            test={test}
            onClick={test.id != null ? () => showTestDetails(test.id, test.run_id, test) : undefined}
          />
        ))}
      </div>
    </div>
  );
}

export default TestsTab;
