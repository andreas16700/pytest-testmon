import React, {useState, useEffect} from "react";
import Header from "./components/Header.jsx";
import SelectorBar from "./components/SelectorBar.jsx";
import Modal from "./components/Modal.jsx";
import MainContent from "./components/MainContent.jsx";
import TestDetails from "./components/TestDetails.jsx";
import FileDetails from "./components/FileDetails.jsx";
import WorkflowFilePopup from "./components/WorkflowFilePopup.jsx";

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
    const [isSidebarOpen, setIsSidebarOpen] = useState(true);
    const [userOtherRepos, setUserOtherRepos] = useState(null);
    const [summary, setSummary] = useState([]);
    const [allTests, setAllTests] = useState([]);
    const [allFiles, setAllFiles] = useState([]);
    const [isPopupWindowOpen, setIsPopupWindowOpen] = useState(false);
    const [workflowFile, setWorkflowFile] = useState("");
    const [originalWorkflowFile, setOriginalWorkflowFile] = useState("");
    const [popupRepo, setPopupRepo] = useState(null);
    const [popupFilePath, setPopupFilePath] = useState("");

    const [activeTab, setActiveTab] = useState("summary");
    const [testSearch, setTestSearch] = useState("");
    const [fileSearch, setFileSearch] = useState("");

    const [modal, setModal] = useState({open: false, title: "", content: null});

    useEffect(() => {
        fetchUser();
    }, []);

    useEffect(() => {
        if (user) {
            loadRepos();
        }
    }, [user]);

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
            const response = await fetch("/auth/user", {credentials: "include"});
            const userData = await response.json();
            setUser(userData);
        } catch (err) {
            console.error("Failed to fetch user:", err);
        }
    };

    const loadRepos = async () => {
        try {
            const systemAndUserReposDataRes = await fetch("/api/repos", {credentials: "include"});
            const systemAndUserReposData = await systemAndUserReposDataRes.json();
            const systemRepos = systemAndUserReposData.system_repos || [];
            const userRepos = systemAndUserReposData.user_repos || [];
            setRepos(systemRepos);
            const systemRepoNames = new Set(systemRepos.map((r) => r.name));
            const missing = userRepos.filter((repo) => !systemRepoNames.has(repo.full_name)); // user repositories that are not listed in the selection bar
            setUserOtherRepos(missing);
        } catch (err) {
            console.error("Failed to load repositories:", err);
            setError("Failed to load repositories");
        }
    };

    const generateWorkflowFile = async (repo, workflow) => {
        try {
            setPopupRepo(repo);
            const content = await fetchWorkflowContent(user.login, repo.name, workflow.path);
            setOriginalWorkflowFile(content);
            setPopupFilePath(workflow.path);
            const aiResponse = await fetch("/api/ask_ai", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({content: content}),
            });
            if (!aiResponse.ok) {
                throw new Error(`Server error: ${aiResponse.status}`);
            }

            const data = await aiResponse.json();
            setWorkflowFile(data.content);
            setIsPopupWindowOpen(true);
        } catch (err) {
            console.error(err);
        }
    };

    const fetchWorkflowContent = async (owner, repo, filePath) => {
        try {
            const encodedPath = encodeURIComponent(filePath);

            const response = await fetch(
                `/api/repos/${owner}/${repo}/contents?path=${encodedPath}`
            );

            if (!response.ok) throw new Error("Failed to fetch content");

            const data = await response.json();
            return data.content;

        } catch (err) {
            console.error(err);
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
                    fetch(`/api/data/${currentRepo}/${currentJob}/${lastRunId}/summary`, {credentials: "include"}).then((r) => r.json()),
                    fetch(`/api/data/${currentRepo}/${currentJob}/${lastRunId}/tests`, {credentials: "include"}).then((r) => r.json()),
                    fetch(`/api/data/${currentRepo}/${currentJob}/${lastRunId}/files`, {credentials: "include"}).then((r) => r.json()),
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
            const resp = await fetch(`/api/data/${currentRepo}/${currentJob}/${run_id}/test/${testId}`, {credentials: "include"});
            const data = await resp.json();

            setModal({
                open: true,
                title: data.test.test_name,
                content: <TestDetails currentRepo={currentRepo} test={data.test} dependencies={data.dependencies}
                                      coverage={data.coverage}/>,
            });
        } catch (err) {
            alert("Failed to load test details: " + err.message);
        }
    };

    const showFileDetails = async (filename, run_id) => {
        try {
            const resp = await fetch(`/api/data/${currentRepo}/${currentJob}/${run_id}/fileDetails/${filename}`, {credentials: "include"});
            const data = await resp.json();
            setModal({
                open: true,
                title: filename,
                content: <FileDetails filename={filename} files={data.files}/>,
            });
        } catch (err) {
            alert("Failed to load file details : " + err.message);
        }
    };

    const handleLogout = async () => {
        try {
            await fetch("/auth/logout", {method: "POST", credentials: "include"});
            window.location.href = "/";
        } catch (err) {
            console.error("Logout failed:", err);
        }
    };

    const selectedRepo = repos.find((r) => r.id === currentRepo);
    const selectedJob = selectedRepo && selectedRepo.jobs.find((r) => r.id === currentJob);

    return (
        <div className="app-root-container">
            <Header user={user} handleLogout={handleLogout}/>

            <div className="app-main-layout flex h-screen overflow-hidden relative">

                {/* 1. LEFT SIDEBAR CONTAINER */}
                <div
                    className="flex-shrink-0 bg-gray-50 border-r border-gray-200"
                    style={{
                        width: isSidebarOpen ? '320px' : '0px',
                        transition: 'width 0.4s ease-in-out',
                        overflow: 'hidden',
                        opacity: isSidebarOpen ? 1 : 0,
                    }}
                >
                    <div style={{width: '320px', height: '100%'}}>
                        <aside
                            className="app-sidebar h-full overflow-y-auto shadow-none rounded-none border-none w-full">
                            <div className="sidebar-header p-4">
                                <h2 className="sidebar-title text-xl font-bold">Project selection</h2>
                                <p className="sidebar-subtitle text-sm text-gray-500">Configure repo and runs</p>
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
                                userOtherRepos={userOtherRepos}
                                generateWorkflowFile={generateWorkflowFile}
                            />
                        </aside>
                    </div>
                </div>

                <button
                    onClick={() => setIsSidebarOpen(!isSidebarOpen)}
                    className="sidebar-toggle-btn"
                    style={{
                        width: '24px',
                        height: '24px',
                        // Logic: Base padding (p-5 = 1.25rem) + Sidebar Width
                        left: isSidebarOpen ? 'calc(320px + 1.25rem)' : '1.25rem',
                        transform: 'translateX(-50%)', // Centers button on the line
                        transition: 'left 0.4s ease-in-out' // Matches sidebar speed
                    }}
                    title={isSidebarOpen ? "Close Sidebar" : "Open Sidebar"}
                >
                    <svg
                        xmlns="http://www.w3.org/2000/svg"
                        width="14"
                        height="14"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="3"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        style={{
                            transform: isSidebarOpen ? 'rotate(0deg)' : 'rotate(180deg)',
                            transition: 'transform 0.4s ease'
                        }}
                    >
                        <path d="M15 18l-6-6 6-6"/>
                    </svg>
                </button>

                {/* 2. RIGHT MAIN: Data Visualization and Tabs */}
                <main className="app-content-area flex-1 overflow-hidden">
                    {loading ? (
                        <div className="app-loading-state">
                            <div className="text-center">
                                <div className="loading-spinner-large"/>
                                <p className="loading-text">Loading testmon data...</p>
                            </div>
                        </div>
                    ) : error ? (
                        <div className="app-error-state">
                            <div className="error-message-box">
                                <div className="flex items-center gap-2">
                                    <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                                        <path fillRule="evenodd"
                                              d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z"
                                              clipRule="evenodd"/>
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
                onClose={() => setModal({open: false, title: "", content: null})}
            >
                {modal.content}
            </Modal>

            {isPopupWindowOpen && <WorkflowFilePopup workflowFile={workflowFile} originalWorkflowFile={originalWorkflowFile} setIsPopupWindowOpen={setIsPopupWindowOpen} user={user} repo={popupRepo} filePath={popupFilePath}/>}
        </div>
    );
}

export default App;