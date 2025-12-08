import React from "react";
import { ExternalLink, Github } from "lucide-react";

function FileItem({currentRepo, file, runId, onClick, branch = "main"}) {
    const navigateToGithub = (e) => {
        e.stopPropagation();
        const cleanPath = file.filename.startsWith('./') ? file.filename.slice(2) : file.filename;
        const url = `https://github.com/${currentRepo}/blob/${branch}/${cleanPath}`;
        window.open(url, "_blank", "noopener,noreferrer");
    };

    return (
        <>
            <div
                className="bg-white border-2 border-gray-300 rounded-lg p-5 cursor-pointer transition-all hover:border-indigo-500 hover:shadow-lg hover:-translate-y-0.5"
                onClick={onClick}
            >
                <div className="flex items-center mb-2">
                    <div className="flex items-center gap-2">
                        <span className="bg-gray-100 border border-gray-200 text-gray-600 text-xs font-mono px-2 py-1 rounded-md whitespace-nowrap">
                            <span className="text-gray-400 select-none">#</span>{runId}
                        </span>
                        <div className="text-lg font-semibold text-gray-700 ml-3">{file.filename}</div>
                    </div>
                    <button
                        onClick={navigateToGithub}
                        className="flex items-center ml-3 gap-1 text-xs font-medium text-gray-500 hover:text-indigo-600 hover:bg-indigo-50 px-3 py-1.5 rounded-md transition-colors border border-transparent hover:border-indigo-100"
                        title="View file on GitHub"
                    >
                        <Github className="w-3.5 h-3.5" />
                        <span>View source</span>
                        <ExternalLink className="w-3 h-3 ml-0.5 opacity-50" />
                    </button>
                </div>
                <div className="flex gap-5 text-sm text-gray-600">
                    <span>🧪 {file.test_count} tests</span>
                    <span>🔖 {file.fingerprint_count} fingerprints</span>
                </div>
            </div>
        </>
    );
}

export default FileItem;