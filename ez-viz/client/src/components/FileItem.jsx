import React from "react";

function FileItem({file, runId, onClick}) {
    return (
        <div
            className="bg-white border-2 border-gray-300 rounded-lg p-5 cursor-pointer transition-all hover:border-indigo-500 hover:shadow-lg hover:-translate-y-0.5"
            onClick={onClick}
        >
            <div className="flex items-center mb-2">
                <div className="flex items-center gap-2">
                <span className="bg-gray-100 border border-gray-200 text-gray-600 text-xs font-mono px-2 py-1 rounded-md whitespace-nowrap">
                    <span className="text-gray-400 select-none">#</span>{runId}
                </span>
                </div>
                <div className="text-lg font-semibold text-gray-700 ml-3">{file.filename}</div>
            </div>
            <div className="flex gap-5 text-sm text-gray-600">
                <span>🧪 {file.test_count} tests</span>
                <span>🔖 {file.fingerprint_count} fingerprints</span>
            </div>
        </div>
    );
}

export default FileItem;