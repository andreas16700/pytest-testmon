import React, { useState, useEffect } from 'react';
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
    const [selectedRunId, setSelectedRunId] = useState(currentRuns[0]);
    const [isAdded, setIsAdded] = useState(false);

    const [summary, setSummary] = useState([]);
    const [allTests, setAllTests] = useState([]);
    const [allFiles, setAllFiles] = useState([]);

    const [activeTab, setActiveTab] = useState('summary');
    const [testSearch, setTestSearch] = useState('');
    const [fileSearch, setFileSearch] = useState('');

    const [modal, setModal] = useState({ open: false, title: '', content: null });

    useEffect(() => {
        fetchUser();
        loadRepos();
    }, []);


       useEffect(() => {
        console.log("sety all tests updated" , allTests)
    }, [allTests]);
    useEffect(() => {
        if (currentRepo && currentJob && currentRuns.length > 0) {
            loadData();
        } else {
            setSummary([]);
        }
    }, [currentRepo, currentJob, currentRuns]);

    const fetchUser = async () => {
        try {
            const response = await fetch("/auth/user", {
                credentials: "include"
            });
            const userData = await response.json();
            setUser(userData);
        } catch (err) {
            console.error("Failed to fetch user:", err);
        }
    };

    const loadRepos = async () => {
        try {
            const response = await fetch("/api/repos", {
                credentials: "include"
            });
            const systemData = await response.json();
            console.log("System data is " ,systemData)
            const systemRepos = systemData.repos || [];

            const userRepos = await loggedUserRepos();
            if (!userRepos) {
                setError("Failed to load user repositories");
                return;
            }
            console.log("user repos" , userRepos)
            const userRepoNames = new Set(userRepos.map(r => r.full_name));

            const matching = systemRepos.filter(repo => userRepoNames.has(repo.name));

            setRepos(matching);
        } catch (err) {
            console.error('Failed to load repos:', err);
            setError('Failed to load repositories');
        }
    };

    const loggedUserRepos = async () => {
        try {
            const resp = await fetch("/api/userRepositories", {
                credentials: "include",
            });
            if (!resp.ok) return null;

            return await resp.json();

        } catch (err) {
            console.error("Failed to fetch repositories:", err);
            return null;
        }
    };

    const loadData = async () => {
        if (!currentRepo || !currentJob || currentRuns.length === 0) return;

        setLoading(true);
        setError(null);
        if (isAdded) {
            try {
                const [summaryData, testsData, filesData] = await Promise.all([
                    fetch(`/api/data/${currentRepo}/${currentJob}/${currentRuns[currentRuns.length - 1]}/summary`, {
                        credentials: "include"
                    }).then(r => r.json()),
                    fetch(`/api/data/${currentRepo}/${currentJob}/${currentRuns[currentRuns.length - 1]}/tests`, {
                        credentials: "include"
                    }).then(r => r.json()),
                    fetch(`/api/data/${currentRepo}/${currentJob}/${currentRuns[currentRuns.length - 1]}/files`, {
                        credentials: "include"
                    }).then(r => r.json())
                ]);
                setSummary(prevSummary => [...prevSummary, summaryData]);
                setAllTests(prevTests => [...prevTests, testsData]);
                setAllFiles(prevFiles => [...prevFiles, filesData]);
                setActiveTab('summary');
            } catch (err) {
                setError('Failed to load testmon data: ' + err.message);
            } finally {
                setLoading(false);
                setIsAdded(false);
            }
        } else {
            setLoading(false);
        }
    };

    const showTestDetails = async (testId) => {
        try {
            const response = await fetch(`/api/data/${currentRepo}/${currentJob}/${currentRuns[0]}/test/${testId}`, {
                credentials: "include"
            });
            const data = await response.json();
           
            setModal({
                open: true,
                title: data.test.test_name,
                content: <TestDetails test={data.test} dependencies={data.dependencies} />
            });
        } catch (err) {
            alert('Failed to load test details: ' + err.message);
        }
    };

    const showFileDetails = (filename) => {
        const relatedTests = allTests.filter(t =>
            t.test_name.includes(filename.replace('.py', ''))
        );
        console.log("all tests are" , allTests)
            


        setModal({
            open: true,
            title: filename,
            content: <FileDetails filename={filename} tests={relatedTests} />
        });
    };

    const handleLogout = async () => {
        try {
            await fetch("/auth/logout", {
                method: "POST",
                credentials: "include"
            });
            window.location.href = "/";
        } catch (err) {
            console.error("Logout failed:", err);
        }
    };
    const selectedRepo = repos.find(r => r.id === currentRepo);
    const selectedJob = selectedRepo && selectedRepo.jobs.find(r => r.id === currentJob);




    return (
        <div className="min-h-screen bg-gradient-to-br from-indigo-500 to-purple-600 p-5">
            <div className="max-w-7xl mx-auto bg-white rounded-xl shadow-2xl">
                <Header user={user} handleLogout={handleLogout}/>

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

            <Modal
                open={modal.open}
                title={modal.title}
                onClose={() => setModal({ open: false, title: '', content: null })}
            >
                {modal.content}
            </Modal>
        </div>
    );
}

export default App;