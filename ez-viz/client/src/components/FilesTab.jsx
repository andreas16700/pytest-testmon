import React, { useState, useEffect } from "react";
import { Network, List, X, ChevronDown } from "lucide-react";
import FileItem from "./FileItem.jsx";
import SearchBox from "./SearchBox.jsx";
import FilesDependencyGraphView from "./FilesDependencyGraphView.jsx";

function FilesTab({ currentRepo, allFiles, search, setSearch, showFileDetails, currentRuns, currentJob }) {
  const [showGraph, setShowGraph] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState(null);

  useEffect(() => {
    if (!currentRuns || currentRuns.length === 0) {
      setSelectedRunId(null);
      return;
    }
    setSelectedRunId((prev) => 
      (prev != null && currentRuns.includes(prev)) ? prev : currentRuns[currentRuns.length - 1]
    );
  }, [currentRuns]);

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
      {/* Top Search & Toggle Bar */}
      <div className="flex items-center gap-4 mb-6">
        <div className="flex-1">
          <SearchBox
            value={search}
            onChange={setSearch}
            placeholder="Filter files by name..."
          />
        </div>
        
        <button className="view-toggle-btn" onClick={() => setShowGraph((v) => !v)}>
          {showGraph ? <List size={18} /> : <Network size={18} />}
          <span>{showGraph ? "Show List" : "Dependency Graph"}</span>
        </button>
      </div>

      {/* Main File List */}
      <div className="grid gap-3">
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

      {/* Dependency Graph Logic Visualization */}
      

      {/* Full-screen Graph Modal */}
      {showGraph && (
        <div className="graph-modal-overlay">
          <div className="graph-modal-container">
            {/* Modal Header */}
            <div className="graph-header">
              <div className="flex items-center gap-6">
                <div className="flex items-center gap-2 text-indigo-600">
                  <Network size={24} />
                  <h2 className="font-bold text-xl tracking-tight text-gray-900">
                    File Dependency Graph
                  </h2>
                </div>

                {currentRuns?.length > 0 && (
                  <div className="flex items-center gap-3">
                    <span className="text-xs font-bold text-gray-400 uppercase tracking-widest">Active Run</span>
                    <select
                      className="run-select-pill cursor-pointer"
                      value={selectedRunId ?? ""}
                      onChange={(e) => setSelectedRunId(Number(e.target.value))}
                    >
                      {currentRuns.map((run) => (
                        <option key={run} value={run}>Run #{run}</option>
                      ))}
                    </select>
                  </div>
                )}
              </div>

              <button
                className="p-2 hover:bg-gray-200 rounded-full transition-colors text-gray-500"
                onClick={() => setShowGraph(false)}
              >
                <X size={24} />
              </button>
            </div>

            {/* Modal Body */}
            <div className="flex-1 bg-slate-50 relative overflow-hidden">
              {selectedRunId != null ? (
                <FilesDependencyGraphView
                  repoId={currentRepo}
                  jobId={currentJob}
                  runId={selectedRunId}
                  onOpenFile={(filename) => showFileDetails(filename, selectedRunId)}
                  height={window.innerHeight * 0.85}
                />
              ) : (
                <div className="h-full flex flex-col items-center justify-center text-gray-400 gap-2">
                  <Network size={48} className="opacity-20" />
                  <p>No run data available for graph generation.</p>
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