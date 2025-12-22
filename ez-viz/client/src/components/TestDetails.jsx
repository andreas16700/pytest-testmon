import React from "react";
import EnvItem from "./EnvItem.jsx";
import { formatDuration } from "./utils.jsx";

function TestDetails({ test, dependencies }) {
  return (
    <>
      <div className="test-details-env-section">
        <EnvItem
          label="Status"
          value={
            test?.failed
              ? "Failed"
              : test?.forced === 0
              ? "Executed"
              : test?.forced === 1
              ? "Forced"
              : "Skipped"
          }
        />

        <EnvItem label="Duration" value={formatDuration(test.duration)} />
      </div>

      <div className="test-details-dependencies-section">
        <h3 className="dependencies-heading">
          Dependencies ({dependencies.length})
        </h3>
        {dependencies.map((dep, idx) => (
          <div key={idx} className="dependency-card">
            <div className="dependency-filename">📄 {dep.filename}</div>
            <div className="dependency-meta">
              <span>SHA: {dep.fsha ? dep.fsha.substring(0, 8) : "N/A"}</span>
              <span>{dep.checksums.length} blocks</span>
            </div>
            <div className="dependency-checksums">
              Checksums: [{dep.checksums.join(", ")}]
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

export default TestDetails;
