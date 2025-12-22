import React, { useState, useEffect } from "react";
import Header from "./components/Header.jsx";
import SelectorBar from "./components/SelectorBar.jsx";
import Modal from "./components/Modal.jsx";
import MainContent from "./components/MainContent.jsx";
import TestDetails from "./components/TestDetails.jsx";
import FileDetails from "./components/FileDetails.jsx";

function App() {
  const [user, setUser] = useState(null);
  const [repos, setRepos] = useState([]);
  const [currentRepo, setCurrentRepo] = useState(null);
  const [currentJob, setCurrentJob] = useState(null);
  const [currentRuns, setCurrentRuns] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [selectedRunId, setSelectedRunId] = useState(null);
  const [isAdded, setIsAdded] = useState(false);

  const [summary, setSummary] = useState([]);
  const [allTests, setAllTests] = useState([]);
  const [allFiles, setAllFiles] = useState([]);

  const [activeTab, setActiveTab] = useState("summary");
  const [testSearch, setTestSearch] = useState("");
  const [fileSearch, setFileSearch] = useState("");

  const [modal, setModal] = useState({ open: false, title: "", content: null });

  useEffect(() => {
    fetchUser();
    loadRepos();
  }, []);

  useEffect(() => {
    if (currentRepo && currentJob && currentRuns.length > 0) {
      loadData();
    } else {
      setSummary([]);
    }
  }, [currentRuns]);

  useEffect(() => {
    setAllTests([]);
    setAllFiles([]);
  }, [currentRepo, currentJob]);

  const fetchUser = async () => {
    try {
      const response = await fetch("/auth/user", { credentials: "include" });
      const userData = await response.json();
      setUser(userData);
    } catch (err) {
      console.error("Failed to fetch user:", err);
    }
  };

  const loadRepos = async () => {
    try {
      const response = await fetch("/api/repos", { credentials: "include" });
      const systemData = await response.json();
      const systemRepos = systemData.repos || [];
      const userRepos = await loggedUserRepos();
      if (!userRepos) {
        setError("Failed to load user repositories");
        return;
      }
      const userRepoNames = new Set(userRepos.map((r) => r.full_name));
      const matching = systemRepos.filter((repo) => userRepoNames.has(repo.name));
      setRepos(matching);
    } catch (err) {
      setError("Failed to load repositories");
    }
  };

  const loggedUserRepos = async () => {
    try {
      const resp = await fetch("/api/userRepositories", { credentials: "include" });
      return resp.ok ? await resp.json() : null;
    } catch (err) {
      return null;
    }
  };

  const loadData = async () => {
    if (!currentRepo || !currentJob || currentRuns.length === 0) return;
    setLoading(true);
    setError(null);
    if (isAdded) {
      try {
        const lastRunId = currentRuns[currentRuns.length - 1];
        const [summaryData, testsData, filesData] = await Promise.all([
          fetch(`/api/data/${currentRepo}/${currentJob}/${lastRunId}/summary`, { credentials: "include" }).then((r) => r.json()),
          fetch(`/api/data/${currentRepo}/${currentJob}/${lastRunId}/tests`, { credentials: "include" }).then((r) => r.json()),
          fetch(`/api/data/${currentRepo}/${currentJob}/${lastRunId}/files`, { credentials: "include" }).then((r) => r.json()),
        ]);
        setSummary((prev) => [...prev, summaryData]);
        setAllTests((prev) => [...prev, testsData]);
        setAllFiles((prev) => [...prev, filesData]);
        setActiveTab("summary");
      } catch (err) {
        setError("Failed to load testmon data: " + err.message);
      } finally {
        setLoading(false);
        setIsAdded(false);
      }
    } else {
      setLoading(false);
    }
  };

  const showTestDetails = async (testId, run_id) => {
    try {
      const resp = await fetch(`/api/data/${currentRepo}/${currentJob}/${run_id}/test/${testId}`, { credentials: "include" });
      const data = await resp.json();
      setModal({
        open: true,
        title: data.test.test_name,
        content: <TestDetails test={data.test} dependencies={data.dependencies} />,
      });
    } catch (err) {
      alert("Failed to load test details: " + err.message);
    }
  };

  const showFileDetails = async (filename, run_id) => {
    try {
      const resp = await fetch(`/api/data/${currentRepo}/${currentJob}/${run_id}/fileDetails/${filename}`, { credentials: "include" });
      const data = await resp.json();
      setModal({
        open: true,
        title: filename,
        content: <FileDetails filename={filename} files={data.files} />,
      });
    } catch (err) {
      alert("Failed to load file details : " + err.message);
    }
  };

  const handleLogout = async () => {
    try {
      await fetch("/auth/logout", { method: "POST", credentials: "include" });
      window.location.href = "/";
    } catch (err) {
      console.error("Logout failed:", err);
    }
  };

  const selectedRepo = repos.find((r) => r.id === currentRepo);
  const selectedJob = selectedRepo && selectedRepo.jobs.find((r) => r.id === currentJob);

  return (
    <div className="app-root-container">
      {/* Top Header */}
      <Header user={user} handleLogout={handleLogout} />

      {/* Main Layout Wrapper */}
      <div className="app-main-layout">
        
        {/* LEFT SIDEBAR: Controls and Selectors */}
        <aside className="app-sidebar">
          <div className="sidebar-header">
            <h2 className="sidebar-title">Project selection</h2>
            <p className="sidebar-subtitle">Configure repo and runs</p>
          </div>
          
          <SelectorBar
            repos={repos}
            currentRepo={currentRepo}
            currentJob={currentJob}
            currentRuns={currentRuns}
            selectedRepo={selectedRepo}
            selectedJob={selectedJob}
            onRepoChange={setCurrentRepo}
            onJobChange={setCurrentJob}
            onRunChange={setCurrentRuns}
            onRefresh={loadRepos}
            setIsAdded={setIsAdded}
            setSummary={setSummary}
            setAllTests={setAllTests}
            setAllFiles={setAllFiles}
            selectedRunId={selectedRunId}
            setSelectedRunId={setSelectedRunId}
          />
        </aside>

        {/* RIGHT MAIN: Data Visualization and Tabs */}
        <main className="app-content-area">
          {loading ? (
            <div className="app-loading-state">
              <div className="text-center">
                <div className="loading-spinner-large" />
                <p className="loading-text">Loading testmon data...</p>
              </div>
            </div>
          ) : error ? (
            <div className="app-error-state">
              <div className="error-message-box">
                <div className="flex items-center gap-2">
                  <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
                  </svg>
                  <span>{error}</span>
                </div>
              </div>
            </div>
          ) : (
            <div className="content-scroll-container">
              <MainContent
                loading={loading}
                error={error}
                summary={summary}
                allTests={allTests}
                allFiles={allFiles}
                activeTab={activeTab}
                setActiveTab={setActiveTab}
                testSearch={testSearch}
                setTestSearch={setTestSearch}
                fileSearch={fileSearch}
                setFileSearch={setFileSearch}
                showTestDetails={showTestDetails}
                showFileDetails={showFileDetails}
                currentRepo={currentRepo}
                currentJob={currentJob}
                currentRuns={currentRuns}
                selectedRunId={selectedRunId}
                setSelectedRunId={setSelectedRunId}
                repos={repos}
              />
            </div>
          )}
        </main>
      </div>

      {/* Global Modal for Details */}
      <Modal
        open={modal.open}
        title={modal.title}
        onClose={() => setModal({ open: false, title: "", content: null })}
      >
        {modal.content}
      </Modal>
    </div>
  );
}

export default App;