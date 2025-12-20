import Tabs from "./Tabs.jsx";
import TestsTab from "./TestsTab.jsx";
import FilesTab from "./FilesTab.jsx";
import SummaryTab from "./SummaryTab.jsx";
import React, { useState } from "react";
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
  repos,
}) {
  if (loading) {
    return (
      <div className="flex flex-col justify-center items-center p-6 max-w-2xl mx-auto h-64">
        <div className="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-blue-600"></div>
        <span className="text-gray-600 font-medium mt-3">Loading...</span>
      </div>
    );
  }

  if (error) {
    return <div className="text-center p-16 text-red-600 text-lg">{error}</div>;
  }

  if (summary.length === 0) {
    return (
      <div className="text-center p-16 text-gray-500 text-xl">
        Select a repository and job to view testmon data.
      </div>
    );
  }

  return (
    <>
      <Tabs
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        testCount={allTests.reduce(
          (total, run) => total + (run.tests?.length || 0),
          0
        )}
        fileCount={allFiles.reduce(
          (total, run) => total + (run.files?.length || 0),
          0
        )}
      />

      <div className="p-8">
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
            allFiles={allFiles}
            search={fileSearch}
            setSearch={setFileSearch}
            showFileDetails={showFileDetails}
            currentRuns={currentRuns}
            currentJob={currentJob}
        
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
      </div>
    </>
  );
}

export default MainContent;
