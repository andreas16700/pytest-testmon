import React from "react";
import FileItem from "./FileItem.jsx";
import SearchBox from "./SearchBox.jsx";

function FilesTab({ allFiles, search, setSearch, showFileDetails }) {
    const filteredFiles = allFiles.map(runData => ({
        ...runData,
        files: runData.files.filter(file =>
            file.filename.toLowerCase().includes(search.toLowerCase())
        )
    })).filter(runData => runData => runData.files.length > 0);

    return (
        <div className="animate-fadeIn">
            <SearchBox
                value={search}
                onChange={setSearch}
                placeholder="🔍 Search files..."
            />

            <div className="grid gap-4">
                {filteredFiles.map(runData => runData.files.map(file => (
                    <FileItem key={file.filename} runId={runData.run_id} file={file} onClick={() => showFileDetails(file.filename)} />
                )))}
            </div>
        </div>
    );
}

export default FilesTab;