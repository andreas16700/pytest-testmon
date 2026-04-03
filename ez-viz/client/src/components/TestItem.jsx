import { formatDuration } from "./utils.jsx";

const STATUS_CONFIG = {
  failed:     { cls: "status-failed",  label: "Failed" },
  skipped:    { cls: "status-skipped", label: "Skipped" },
  deselected: { cls: "status-skipped", label: "Skipped" },
  passed:     { cls: "status-success", label: "Passed" },
  executed:   { cls: "status-success", label: "Executed" },
  forced:     { cls: "status-forced",  label: "Forced" },
};

function TestItem({ runId, test, onClick }) {
  const { cls, label } = STATUS_CONFIG[test.status] || STATUS_CONFIG.executed;

  return (
    <div className={`test-item-card${onClick ? "" : " test-item-card--no-click"}`} onClick={onClick}>
      <div className="test-item-header">
        {runId != null && (
          <div className="run-id-wrapper">
            <span className="run-id-badge">
              <span className="hash-symbol">#</span>
              {runId}
            </span>
          </div>
        )}
        <div className="test-name">{test.name}</div>
        <span className={`status-badge ${cls}`}>{label}</span>
      </div>
      <div className="test-item-footer">
        <span>{formatDuration(test.duration)}</span>
        {test.lineno != null && (
          <span className="test-lineno">Line No: {test.lineno + 1}</span>
        )}
        {test.status === "failed" && test.error_message && (
          <span className="test-error-hint" title={test.longrepr || test.error_message}>
            {test.error_message.slice(0, 80)}
          </span>
        )}
      </div>
    </div>
  );
}

export default TestItem;
