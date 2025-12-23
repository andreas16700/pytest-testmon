import React from "react";
import { ExternalLink, Github, Beaker, Fingerprint } from "lucide-react";

function FileItem({ currentRepo, file, runId, onClick, branch = "main" }) {
    
    // Logic to split path for better styling
    const pathParts = file.filename.split('/');
    const fileName = pathParts.pop();
    const directory = pathParts.join('/');

    const navigateToGithub = (e) => {
        e.stopPropagation();
        const cleanPath = file.filename.startsWith('./') ? file.filename.slice(2) : file.filename;
        const url = `https://github.com/${currentRepo}/blob/${branch}/${cleanPath}`;
        window.open(url, "_blank", "noopener,noreferrer");
    };

    return (
        <div className="file-item-card" onClick={onClick}>
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3 overflow-hidden">
                    <span className="file-badge-run">
                        RUN {runId}
                    </span>
                    
                    <div className="text-base truncate">
                        {directory && <span className="file-path-dir">{directory}/</span>}
                        <span className="file-path-name">{fileName}</span>
                    </div>
                </div>

                <button
                    onClick={navigateToGithub}
                    className="btn-github-link shrink-0"
                    title="View file on GitHub"
                >
                    <Github size={14} />
                    <span>View source</span>
                    <ExternalLink size={12} className="opacity-50" />
                </button>
            </div>

            <div className="flex gap-6 pt-1">
                <div className="flex items-center gap-1.5 text-xs font-medium text-slate-500">
                    <Beaker size={14} className="text-indigo-500" />
                    <span>{file.test_count} tests impacted</span>
                </div>
                
                <div className="flex items-center gap-1.5 text-xs font-medium text-slate-500">
                    <Fingerprint size={14} className="text-purple-500" />
                    <span>{file.fingerprint_count} fingerprints</span>
                </div>
            </div>
        </div>
    );
}

export default FileItem;