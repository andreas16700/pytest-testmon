import React from "react";
import { formatDuration } from "./utils.jsx";

function TestItem({ runId, test, onClick }) {
  const getStatusClass = () => {
    if (test.failed) return "status-failed";
    if (test.forced) return "status-forced";
    if (test.forced === 0) return "status-success";
    else return "status-skipped";
  };

  return (
    <div className="test-item-card" onClick={onClick}>
      <div className="test-item-header">
        <div className="run-id-wrapper">
          <span className="run-id-badge">
            <span className="hash-symbol">#</span>
            {runId}
          </span>
        </div>
        <div className="test-name">{test.name}</div>
        <span className={`status-badge ${getStatusClass()}`}>
          {test.failed ? <p>Failed</p> : test.forced ? <p>Forced</p> : test.forced === 0 ?  <p>Executed</p> : <p>Skipped</p>}
        </span>
      </div>
      <div className="test-item-footer">
        <span>{formatDuration(test.duration * 1000)}</span>
        {/* Dependency count will be added here! */}
      </div>
    </div>
  );
}

export default TestItem;
