import React, { useState, useEffect } from "react";
import FileItem from "./FileItem.jsx";
import SearchBox from "./SearchBox.jsx";
import FilesDependencyGraphView from "./FilesDependencyGraphView.jsx";

function FilesTab({
  currentRepo,
  allFiles,
  search,
  setSearch,
  showFileDetails,
  currentRuns,   // e.g. [1915, 1916, ...]
  currentJob,
}) {
  const [showGraph, setShowGraph] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState(null);


  // Whenever currentRuns changes, pick a sensible default run
  useEffect(() => {
    if (!currentRuns || currentRuns.length === 0) {
      setSelectedRunId(null);
      return;
    }

    setSelectedRunId((prev) => {
      // keep previous selection if still present
      if (prev != null && currentRuns.includes(prev)) {
        return prev;
      }
      // otherwise default to the latest run (last in array)
      return currentRuns[currentRuns.length - 1];
    });
  }, [currentRuns]);

  // Filter files by search term (no run filtering on main list)
  const filteredFiles = allFiles
    .map((runData) => ({
      ...runData,
      files: runData.files.filter((file) =>
        file.filename.toLowerCase().includes(search.toLowerCase())
      ),
    }))
    .filter((runData) => runData.files.length > 0);

  return (
    <div className="animate-fadeIn">
      {/* top row */}
      <div className="flex items-center gap-3">
        <div className="flex-1">
          <SearchBox
            value={search}
            onChange={setSearch}
            placeholder="🔍 Search files..."
          />
        </div>

        {/* button top-right */}
        <button
          className="px-3 py-2 rounded-lg border hover:bg-gray-50"
          onClick={() => setShowGraph((v) => !v)}
        >
          {showGraph ? "📄 Show list" : "🕸️ Show dependency graph"}
        </button>
      </div>

      {/* files list (all runs mixed, filtered only by text) */}
      <div className="grid gap-4 mt-4">
        {filteredFiles.flatMap((runData) =>
          runData.files.map((file) => (
            <FileItem
              currentRepo={currentRepo}
              key={`${runData.run_id}:${file.filename}`}
              runId={runData.run_id}
              file={file}
              onClick={() => showFileDetails(file.filename, runData.run_id)}
            />
          ))
        )}
      </div>

      {/* big modal graph overlay */}
      {showGraph && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="bg-white rounded-2xl shadow-xl w-[96vw] h-[92vh] flex flex-col">
            {/* header */}
            <div className="flex items-center justify-between px-4 py-2 border-b">
              <div className="flex items-center gap-3">
                <h2 className="font-semibold text-lg">
                  🕸️ File dependency graph
                </h2>

                {/* run selector – only visible in the modal */}
                {currentRuns && currentRuns.length > 0 && (
                  <select
                    className="border rounded-md px-2 py-1 text-sm"
                    value={selectedRunId ?? ""}
                    onChange={(e) => setSelectedRunId(Number(e.target.value))}
                  >
                    {currentRuns.map((run) => (
                      <option key={run} value={run}>
                        Run #{run}
                      </option>
                    ))}
                  </select>
                )}
              </div>

              <button
                className="px-3 py-1 rounded-md border text-sm hover:bg-gray-100"
                onClick={() => setShowGraph(false)}
              >
                ✕ Close
              </button>
            </div>

            {/* graph body */}
            <div className="flex-1 overflow-hidden">
              {selectedRunId != null ? (
                <FilesDependencyGraphView
                  repoId={currentRepo}
                  jobId={currentJob}
                  runId={selectedRunId}
                  onOpenFile={(filename) =>
                    showFileDetails(filename, selectedRunId)
                  }
                  height={window.innerHeight * 0.85}
                />
              ) : (
                <div className="h-full flex items-center justify-center text-gray-500 text-sm">
                  No runs available for this job.
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default FilesTab;
