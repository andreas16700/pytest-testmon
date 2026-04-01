import React from "react";
import EnvItem from "./EnvItem.jsx";
import { formatDuration } from "./utils.jsx";

function TestDetails({ currentRepo, test, dependencies, externalPackages, branch = "main" }) {
  const deps = dependencies || [];

  const navigateToGithub = (e, filename) => {
    e.stopPropagation();
    if (!currentRepo || !filename) return;

    const cleanPath = filename.startsWith("./") ? filename.slice(2) : filename;
    const url = `https://github.com/${currentRepo}/blob/${branch}/${cleanPath}`;

    window.open(url, "_blank", "noopener,noreferrer");
  };

  return (
    <>
      {/* Status + duration */}
      <div className="test-details-env-section">
        <EnvItem
          label="Status"
          value={test?.failed ? "Failed" : "Executed"}
        />

        <EnvItem
          label="Duration"
          value={formatDuration(test?.duration ?? 0)}
        />
      </div>

      {/* Dependencies + external packages */}
      <div className="test-details-dependencies-section">
        <h3 className="dependencies-heading">
          Dependencies ({deps.length})
        </h3>

        {deps.map((dep, idx) => {
          return (
            <div key={idx} className="dependency-card">
              <div className="dependency-filename">📄 {dep.filename}</div>
              <div className="external-packages">External Packages: {externalPackages.join(", ")}</div>
              <div className="dependency-meta">
                <span>
                  <span className="font-medium">SHA:</span>{" "}{dep.fsha ? dep.fsha.substring(0, 8) : "N/A"}
                </span>
                <span>
                  <span className="font-medium">Checksum:</span>{" "}{dep.checksum}
                </span>
              </div>

              {/* View on GitHub button */}
              {currentRepo && (
                <div className="mt-2">
                  <button
                    type="button"
                    className="px-3 py-1 text-sm rounded-md border border-blue-500 text-blue-600 hover:bg-blue-50 transition"
                    onClick={(e) => navigateToGithub(e, dep.filename)}
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
