import React from "react";
import EnvItem from "./EnvItem.jsx";
import { formatDuration } from "./utils.jsx";

function TestDetails({ currentRepo, test, dependencies, externalPackages, branch = "main" }) {
  const deps = dependencies || [];

  const navigateToGithub = (e, filename) => {
    e.stopPropagation();
    if (!currentRepo || !filename) return;
    const cleanPath = filename.startsWith("./") ? filename.slice(2) : filename;
    window.open(`https://github.com/${currentRepo}/blob/${branch}/${cleanPath}`, "_blank", "noopener,noreferrer");
  };

  const status = test?.status
    ? test.status.charAt(0).toUpperCase() + test.status.slice(1)
    : test?.failed ? "Failed" : "Executed";

  const testFile = test?.name?.split("::")[0];
  const ghUrl = currentRepo && testFile
    ? `https://github.com/${currentRepo}/blob/${branch}/${testFile}${test?.lineno ? `#L${test.lineno}` : ""}`
    : null;

  return (
    <>
      {/* Status + Duration */}
      <div className="test-details-env-section">
        <EnvItem label="Status" value={status} />
        <EnvItem label="Duration" value={formatDuration(test?.duration ?? 0)} />
      </div>

      {/* Line No + View on GitHub */}
      {test?.lineno != null && (
        <div className="test-details-location-row">
          <span className="test-location-label">Line No:</span>
          <span className="test-location-value">{test.lineno}</span>
          {ghUrl && (
            <a
              href={ghUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="test-github-link"
            >
              View on GitHub ↗
            </a>
          )}
        </div>
      )}

      {/* Error details */}
      {test?.error_message && (
        <div className="test-details-error-section">
          <h3 className="dependencies-heading">Error</h3>
          <div className="dependency-card">
            <pre className="text-sm text-red-600 font-mono whitespace-pre-wrap">{test.longrepr || test.error_message}</pre>
          </div>
        </div>
      )}

      {/* Dependencies */}
      <div className="test-details-dependencies-section">
        <h3 className="dependencies-heading">Dependencies ({deps.length})</h3>

        {deps.map((dep, idx) => (
          <div key={idx} className="dependency-card">
            <div className="dependency-filename">📄 {dep.filename}</div>
            <div className="external-packages">External Packages: {externalPackages.join(", ")}</div>
            <div className="dependency-meta">
              <span><span className="font-medium">SHA:</span> {dep.fsha ? dep.fsha.substring(0, 8) : "N/A"}</span>
              <span><span className="font-medium">Checksum:</span> {dep.checksum}</span>
            </div>
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
        ))}
      </div>
    </>
  );
}

export default TestDetails;
