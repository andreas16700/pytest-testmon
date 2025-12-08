import React from "react";
import {formatDuration} from "./utils.jsx";

function TestItem({ test, runId, onClick }) {
    const getStatusClass = () => {
        if (test.failed) return 'status-failed';
        if (test.forced) return 'status-skipped';
        return 'status-success';
    };

    return (
        <div
            className="test-item-card"
            onClick={onClick}
        >
            <div className="test-item-header">
                <div className="run-id-wrapper">
                    <span className="run-id-badge">
                        <span className="hash-symbol">#</span>{runId}
                    </span>
                </div>
                <div className="test-name">{test.test_name}</div>
                <span className={`status-badge ${getStatusClass()}`}>
                    {test.forced === 0 ? <p>Executed</p> : <p>Skipped</p>}
                </span>
            </div>
            <div className="test-item-footer">
                <span>{formatDuration(test.duration)}</span>
                <span>{test.dependency_count} dependencies</span>
            </div>
        </div>
    );
}

export default TestItem;