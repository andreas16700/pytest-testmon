import React from "react";
import Tabs from "./Tabs.jsx";
import TestsTab from "./TestsTab.jsx";
import FilesTab from "./FilesTab.jsx";
import SummaryTab from "./SummaryTab.jsx";
import TestManagementTab from "./TestManagementTab.jsx";

function MainContent({
                         loading,
                         error,
                         summary,
                         allTests,
                         allFiles,
                         activeTab,
                         setActiveTab,
                         testSearch,
                         setTestSearch,
                         fileSearch,
                         setFileSearch,
                         showTestDetails,
                         showFileDetails,
                         currentRepo,
                         currentJob,
                         currentRuns,
                         selectedRunId,
                         setSelectedRunId,
                         repos
                     }) {

    if (loading) {
        return (
            <div className="state-container">
                <div className="spinner"></div>
                <span className="text-indigo-600 font-semibold mt-4 tracking-wide uppercase text-xs">
          Fetching Data...
        </span>
            </div>
        );
    }

    if (error) {
        return (
            <div className="state-container">
                <div className="error-state-text">
                    {error}
                </div>
            </div>
        );
    }

    if (summary.length === 0) {
        return (
            <div className="state-container">
                <div className="bg-gray-100 p-8 rounded-full mb-4">
                    <svg className="w-12 h-12 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2"
                              d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
                    </svg>
                </div>
                <p className="empty-state-text">
                    Select a repository and job above to view your testmon analysis.
                </p>
            </div>
        );
    }

    const totalTests = allTests.reduce((total, run) => total + (run.tests?.length || 0), 0);
    const totalFiles = allFiles.reduce((total, run) => total + (run.files?.length || 0), 0);

    return (
        <>
            <Tabs
                activeTab={activeTab}
                setActiveTab={setActiveTab}
                testCount={totalTests}
                fileCount={totalFiles}
            />

            <main className="main-content-wrapper">
                {activeTab === "summary" && (
                    <SummaryTab
                        summary={summary}
                        allTests={allTests}
                        currentRepo={currentRepo}
                        currentJob={currentJob}
                        currentRuns={currentRuns}
                        selectedRunId={selectedRunId}
                        setSelectedRunId={setSelectedRunId}
                    />
                )}

                {activeTab === "tests" && (
                    <TestsTab
                        allTests={allTests}
                        search={testSearch}
                        setSearch={setTestSearch}
                        showTestDetails={showTestDetails}
                    />
                )}

                {activeTab === "files" && (
                    <FilesTab
                        currentRepo={currentRepo}
                        currentJob={currentJob}
                        currentRun={selectedRunId}
                        allFiles={allFiles}
                        search={fileSearch}
                        setSearch={setFileSearch}
                        showFileDetails={showFileDetails}
                    />
                )}

                {activeTab === "management" && (
                    <TestManagementTab
                        repos={repos}
                        currentRepo={currentRepo}
                        currentJob={currentJob}
                        currentRuns={currentRuns}
                    />
                )}
            </main>
        </>
    );
}

export default MainContent;