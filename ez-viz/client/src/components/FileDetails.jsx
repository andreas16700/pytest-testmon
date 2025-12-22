import React from "react";
import { formatDuration, getStatusText } from "./utils.jsx";
import { Clock, ShieldCheck, Activity } from "lucide-react";

function FileDetails({ filename, files }) {
    // Helper to map status text to our CSS classes
    const getStatusClass = (file) => {
        const text = getStatusText(file).toLowerCase();
        if (text.includes('pass')) return 'status-passed';
        if (text.includes('fail')) return 'status-failed';
        return 'status-skipped';
    };

    return (
        <div className="animate-fadeIn">
            <div className="flex items-center gap-3 mb-6 pb-4 border-b border-gray-100">
                <div className="p-2 bg-indigo-50 rounded-lg text-indigo-600">
                    <Activity size={20} />
                </div>
                <div>
                    <h3 className="text-gray-900 font-bold text-lg leading-none">
                        Impact Analysis
                    </h3>
                    <p className="text-gray-500 text-xs mt-1">
                        Tests affected by changes in <code className="text-indigo-600 font-semibold">{filename.split('/').pop()}</code>
                    </p>
                </div>
            </div>

            {files.length > 0 ? (
                <div className="space-y-3">
                    {files.map((file, idx) => (
                        <div key={idx} className="details-test-card border-l-4 border-l-indigo-500">
                            <div className="flex items-start justify-between gap-4">
                                <div className="font-mono text-sm font-semibold text-gray-800 break-all">
                                    {file.test_name}
                                </div>
                                <span className={`status-badge shrink-0 ${getStatusClass(file)}`}>
                                    {getStatusText(file)}
                                </span>
                            </div>
                            
                            <div className="flex items-center gap-4 mt-3 text-xs text-gray-500">
                                <div className="flex items-center gap-1">
                                    <Clock size={12} className="text-gray-400" />
                                    {formatDuration(file.duration)}
                                </div>
                                <div className="flex items-center gap-1">
                                    <ShieldCheck size={12} className="text-gray-400" />
                                    <span>Verified run</span>
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            ) : (
                <div className="py-12 text-center bg-gray-50 rounded-2xl border-2 border-dashed border-gray-200">
                    <p className="text-gray-400 font-medium">No dependent tests found for this file.</p>
                </div>
            )}
        </div>
    );
}

export default FileDetails;