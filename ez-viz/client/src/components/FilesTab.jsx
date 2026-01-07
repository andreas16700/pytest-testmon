import React from "react";
import FileItem from "./FileItem.jsx";
import SearchBox from "./SearchBox.jsx";

function FilesTab({currentRepo, currentJob, allFiles, search, setSearch, showFileDetails}) {

    const handleShowGraph = () => {
        if (!currentRepo || !currentJob) {
            console.error("Cannot show graph: Missing repoId or jobId");
            return;
        }

        try {
            const graphUrl = `/api/dependencyGraph/${currentRepo}/${currentJob}`;
            window.open(graphUrl, "_blank", "noopener, noreferrer");
        } catch (error) {
            console.error("Failed to open dependency graph:", error);
        }
    };

    const filteredFiles = allFiles.map((runData) => ({
            ...runData,
            files: runData.files.filter((file) =>
                file.filename.toLowerCase().includes(search.toLowerCase())
            ),
        })).filter((runData) => runData.files.length > 0);

    return (
        <div className="animate-fadeIn">
            <div className="flex items-center gap-4 mb-6">
                <div className="flex-1">
                    <SearchBox
                        value={search}
                        onChange={setSearch}
                        placeholder="Filter files by name..."
                    />
                </div>
                <div>
                    <button
                        type="button"
                        onClick={handleShowGraph}
                        style={{
                            padding: "8px 16px",
                            backgroundColor: "#007bff",
                            color: "white",
                            border: "none",
                            borderRadius: "4px",
                            cursor: "pointer",
                            fontSize: "14px"
                        }}
                    >
                        Show Dependency Graph
                    </button>
                </div>
            </div>

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
        </div>
    );
}

export default FilesTab;