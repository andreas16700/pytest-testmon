import React, { useState, useMemo } from "react";
import TestItem from "./TestItem.jsx";
import SearchBox from "./SearchBox.jsx";

function TestsTab({ allTests, search, setSearch, showTestDetails }) {
  const [sortByStatus, setSortByStatus] = useState(false);
  const [sortByRuntime, setSortByRuntime] = useState(false);

  const [statusFilter, setStatusFilter] = useState({
    failed: true,
    skipped: true,
    executed: true,
    forced: true,
  });

  const getStatusKey = (test) => {
    if (test.failed) return "failed";
    if (test.forced !== 0 && test.forced !== 1) return "skipped";
    if (test.forced === 0) return "executed";
    if (test.forced === 1) return "forced";
    return "skipped";
  };

  const getStatusOrder = (test) => {
    const key = getStatusKey(test);
    switch (key) {
      case "failed":
        return 0;
      case "skipped":
        return 1;
      case "executed":
        return 2;
      case "forced":
        return 3;
      default:
        return 99;
    }
  };

  const toggleStatusFilter = (key) => {
    setStatusFilter((prev) => ({
      ...prev,
      [key]: !prev[key],
    }));
  };

  const handleSortByStatus = () => {
    setSortByStatus((prev) => !prev);
  };

  const handleSortByRuntime = () => {
    setSortByRuntime((prev) => !prev);
  };

  const tests = useMemo(() => {
    let flat = allTests.flatMap((runData) =>
      runData.tests.map((test) => ({
        ...test,
        run_id: runData.run_id,
      }))
    );

    flat = flat.filter((t) =>
      t.test_name.toLowerCase().includes(search.toLowerCase())
    );

    flat = flat.filter((t) => statusFilter[getStatusKey(t)]);

    if (sortByStatus && sortByRuntime) {
      // Sort by status first, then by runtime within each status group
      flat = [...flat].sort((a, b) => {
        const statusDiff = getStatusOrder(a) - getStatusOrder(b);
        if (statusDiff !== 0) return statusDiff;
        return (b.duration || 0) - (a.duration || 0);
      });
    } else if (sortByStatus) {
      flat = [...flat].sort((a, b) => getStatusOrder(a) - getStatusOrder(b));
    } else if (sortByRuntime) {
      flat = [...flat].sort((a, b) => (b.duration || 0) - (a.duration || 0));
    }

    return flat;
  }, [allTests, search, sortByStatus, sortByRuntime, statusFilter]);

  return (
    <div className="tests-tab-container">
      {/* Search Bar TOP */}
      <div className="tests-search-wrapper">
        <SearchBox
          value={search}
          onChange={setSearch}
          placeholder="🔍 Search tests..."
        />
      </div>

      {/* Controls Bar */}
      <div className="tests-controls-bar">
        {/* Sort Box */}
        <label className="sort-control-label">
          <input
            type="checkbox"
            checked={sortByStatus}
            onChange={handleSortByStatus}
            className="sort-checkbox-input"
          />
          <span className="sort-label-text">Order by status</span>
        </label>

        <label className="sort-control-label">
          <input
            type="checkbox"
            checked={sortByRuntime}
            onChange={handleSortByRuntime}
            className="sort-checkbox-input"
          />
          <span className="sort-label-text">Order by runtime</span>
        </label>

        {/* Status Filters Panel */}
        <div className="status-filter-panel">
          <span className="filter-panel-title">Visible statuses</span>

          <div className="filter-badges-group">
            <label className="status-badge-filter status-badge-failed">
              <input
                type="checkbox"
                checked={statusFilter.failed}
                onChange={() => toggleStatusFilter("failed")}
                className="filter-checkbox-small accent-red-600"
              />
              <span className="font-medium">Failed</span>
            </label>

            <label className="status-badge-filter status-badge-skipped">
              <input
                type="checkbox"
                checked={statusFilter.skipped}
                onChange={() => toggleStatusFilter("skipped")}
                className="filter-checkbox-small accent-yellow-500"
              />
              <span className="font-medium">Skipped</span>
            </label>

            <label className="status-badge-filter status-badge-executed">
              <input
                type="checkbox"
                checked={statusFilter.executed}
                onChange={() => toggleStatusFilter("executed")}
                className="filter-checkbox-small accent-green-600"
              />
              <span className="font-medium">Executed</span>
            </label>

            <label className="status-badge-filter status-badge-forced">
              <input
                type="checkbox"
                checked={statusFilter.forced}
                onChange={() => toggleStatusFilter("forced")}
                className="filter-checkbox-small accent-purple-600"
              />
              <span className="font-medium">Forced</span>
            </label>
          </div>
        </div>
      </div>

      {/* Test List */}
      <div className="test-list-grid">
        {tests.map((test) => (
          <TestItem
            key={`${test.id}-${test.run_id}`}
            runId={test.run_id}
            test={test}
            onClick={() => showTestDetails(test.id, test.run_id)}
          />
        ))}
      </div>
    </div>
  );
}

export default TestsTab;
