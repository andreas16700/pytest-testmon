import React from "react";

function Tabs({ activeTab, setActiveTab, testCount, fileCount }) {
    const tabs = [
        { id: 'summary', label: 'Summary' },
        { id: 'tests', label: `Tests (${testCount})` },
        { id: 'files', label: `Files (${fileCount})` },
        { id: 'management', label: 'Test Management'}
    ];

    return (
        <div className="tabs-container">
            {tabs.map(tab => (
                <button
                    key={tab.id}
                    className={`tab-button ${
                        activeTab === tab.id
                            ? 'tab-button-active'
                            : 'tab-button-inactive'
                    }`}
                    onClick={() => setActiveTab(tab.id)}
                >
                    {tab.label}
                    {activeTab === tab.id && (
                        <span className="tab-indicator" />
                    )}
                </button>
            ))}
        </div>
    );
}

export default Tabs;