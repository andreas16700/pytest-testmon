import React from "react";
import {formatDuration, getStatusText} from "./utils.jsx";

function FileDetails({ filename, files }) {
    return (
        <div>
            <h3 className="text-gray-700 mb-4 pb-2 border-b-2 border-gray-200 text-xl">
                Tests depending on this file
            </h3>
            {files.length > 0 ? (
                files.map((file, idx) => (
                    <div key={idx} className="bg-gray-50 p-4 rounded-lg mb-3 border-l-4 border-indigo-500">
                        <div className="font-semibold text-gray-700 mb-2">{file.test_name}</div>
                        <div className="text-sm text-gray-600 flex gap-5">
                            <span>{getStatusText(file)}</span>
                            <span>{formatDuration(file.duration)}</span>
                        </div>
                    </div>
                ))
            ) : (
                <p className="text-gray-500">No tests found</p>
            )}
        </div>
    );
}

export default FileDetails;