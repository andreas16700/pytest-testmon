import React from "react";
import EnvItem from "./EnvItem.jsx";
import { formatDuration } from "./utils.jsx";

function TestDetails({ currentRepo, test, dependencies, coverage, branch = "main" }) {
  // Safety defaults
  const deps = dependencies || [];
  const cov = coverage || {};

  const navigateToGithub = (e, filename, lines) => {
    e.stopPropagation();
    if (!currentRepo || !filename || !lines || lines.length === 0) return;

    const sorted = [...lines].sort((a, b) => a - b);
    const first = sorted[0];
    const last = sorted[sorted.length - 1];

    let anchor = `#L${first}`;
    if (sorted.length > 1 && last !== first) {
      anchor = `#L${first}-L${last}`;
    }

    const cleanPath = filename.startsWith("./") ? filename.slice(2) : filename;
    const url = `https://github.com/${currentRepo}/blob/${branch}/${cleanPath}${anchor}`;

    window.open(url, "_blank", "noopener,noreferrer");
  };

  return (
    <>
      {/* Status + duration */}
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

        <EnvItem
          label="Duration"
          value={formatDuration(test?.duration ?? 0)}
        />
      </div>

      {/* Dependencies + coverage lines */}
      <div className="test-details-dependencies-section">
        <h3 className="dependencies-heading">
          Dependencies ({deps.length})
        </h3>

        {deps.map((dep, idx) => {
          const linesForFile = cov[dep.filename] || [];

          return (
            <div key={idx} className="dependency-card">
              <div className="dependency-filename">📄 {dep.filename}</div>

              <div className="dependency-meta">
                <span>
                  SHA: {dep.fsha ? dep.fsha.substring(0, 8) : "N/A"}
                </span>
                <span>{dep.checksums?.length ?? 0} blocks</span>
              </div>

              {/* Covered lines */}
              <div className="dependency-checksums">
                {linesForFile.length > 0 ? (
                  <>Lines: [{linesForFile.join(", ")}]</>
                ) : (
                  <span className="text-gray-500">
                    No coverage lines recorded for this file in this test
                  </span>
                )}
              </div>

              {/* View on GitHub button if we have repo + coverage lines */}
              {currentRepo && linesForFile.length > 0 && (
                <div className="mt-2">
                  <button
                    type="button"
                    className="px-3 py-1 text-sm rounded-md border border-blue-500 text-blue-600 hover:bg-blue-50 transition"
                    onClick={(e) => navigateToGithub(e, dep.filename, linesForFile)}
                  >
                    View on GitHub
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}

export default TestDetails;
