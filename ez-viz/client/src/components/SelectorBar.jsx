import {RefreshCw, ChevronDown, Loader2, FileCode, AlertCircle} from "lucide-react";
import React, {useState, useRef, useEffect} from "react";

function SelectorBar({repos, currentRepo, currentJob, currentRuns, selectedRepo, selectedJob, onRepoChange, onJobChange, onRunChange, onRefresh, setIsAdded, setSummary, setAllTests, setAllFiles, selectedRunId, setSelectedRunId, userOtherRepos, generateWorkflowFile}) {
    const [isRepoDropdownOpen, setIsRepoDropdownOpen] = useState(false);
    const [isJobDropdownOpen, setIsJobDropdownOpen] = useState(false);
    const [isRunDropdownOpen, setIsRunDropdownOpen] = useState(false);
    const [isFutureRepoDropdownOpen, setIsFutureRepoDropdownOpen] = useState(false);
    const [isWorkflowDropdownOpen, setIsWorkflowDropdownOpen] = useState(false);

    const [selectedOtherRepo, setSelectedOtherRepo] = useState(null);
    const [workflowFiles, setWorkflowFiles] = useState([]);
    const [selectedWorkflow, setSelectedWorkflow] = useState(null);

    const [isGenerating, setIsGenerating] = useState(false);
    const [isLoadingWorkflows, setIsLoadingWorkflows] = useState(false);
    const [workflowStatus, setWorkflowStatus] = useState('idle'); // idle, loading, no_workflows, ready, no_pytest, multiple_selection

    const repoRef = useRef(null);
    const jobRef = useRef(null);
    const runRef = useRef(null);
    const userRepoRef = useRef(null);
    const workflowRef = useRef(null);

    useEffect(() => {
        function handleClickOutside(event) {
            if (repoRef.current && !repoRef.current.contains(event.target)) setIsRepoDropdownOpen(false);
            if (jobRef.current && !jobRef.current.contains(event.target)) setIsJobDropdownOpen(false);
            if (runRef.current && !runRef.current.contains(event.target)) setIsRunDropdownOpen(false);
            if (userRepoRef.current && !userRepoRef.current.contains(event.target)) setIsFutureRepoDropdownOpen(false);
            if (workflowRef.current && !workflowRef.current.contains(event.target)) setIsWorkflowDropdownOpen(false);
        }

        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    const handleOtherRepoSelect = async (otherRepo) => {
        console.log(otherRepo)
        setSelectedOtherRepo(otherRepo);
        setIsFutureRepoDropdownOpen(false);

        setWorkflowFiles([]);
        setSelectedWorkflow(null);
        setWorkflowStatus('loading');
        setIsLoadingWorkflows(true);

        try {
            // Returns: [{ name: 'ci.yml', path: '.github/...', uses_pytest: boolean }]
            const workflowFilesRes = await fetch(`/api/repos/${otherRepo.owner}/${otherRepo.name}/actions/workflows`, {credentials: "include"});
            const workflowFiles = await workflowFilesRes.json();
            setWorkflowFiles(workflowFiles || []);

            if (!workflowFiles || workflowFiles.length === 0) {
                setWorkflowStatus('no_workflows');
            } else if (workflowFiles.length === 1) {
                const workflowFile = workflowFiles[0];
                setSelectedWorkflow(workflowFile);
                setWorkflowStatus(workflowFile.uses_pytest ? 'ready' : 'no_pytest');
            } else {
                setWorkflowStatus('multiple_selection');
            }
        } catch (error) {
            console.error("Failed to fetch workflows", error);
            setWorkflowStatus('no_workflows');
        } finally {
            setIsLoadingWorkflows(false);
        }
    };

    const handleWorkflowFileSelection = (workflowFile) => {
        setSelectedWorkflow(workflowFile);
        setIsWorkflowDropdownOpen(false);
    };

    const handleGenerateClick = async () => {
        if (!selectedOtherRepo || !selectedWorkflow || !selectedWorkflow.uses_pytest) return;
        setIsGenerating(true);
        await generateWorkflowFile(selectedOtherRepo, selectedWorkflow);
        setIsGenerating(false);
    };

    const isSelectionValid = selectedWorkflow && selectedWorkflow.uses_pytest;

    return (
        <div className="selector-bar">
            {/* Repository Selector */}
            <div className="selector-group" ref={repoRef}>
                <label className="selector-label">Repository</label>
                <button
                    type="button"
                    className="dropdown-trigger"
                    onClick={() => setIsRepoDropdownOpen(!isRepoDropdownOpen)}
                    disabled={repos.length === 0}
                >
                    <span className="truncate">
                        {currentRepo ?
                            `${currentRepo} (${repos.find(r => r.id === currentRepo)?.jobs.length || 0} jobs)` :
                            'Select a repository'}
                    </span>
                    <ChevronDown className={`dropdown-icon ${isRepoDropdownOpen ? 'rotate-180' : ''}`}/>
                </button>

                {isRepoDropdownOpen && (
                    <div className="dropdown-menu">
                        {repos.map(repo => (
                            <div
                                key={repo.id}
                                className={`dropdown-item ${currentRepo === repo.id ? 'dropdown-item-active' : ''}`}
                                onClick={() => {
                                    onRepoChange(repo.id);
                                    onJobChange(null);
                                    onRunChange([]);
                                    setIsRepoDropdownOpen(false);
                                }}
                            >
                                {repo.name} ({repo.jobs.length} jobs)
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {/* Job Selector */}
            <div className="selector-group" ref={jobRef}>
                <label className="selector-label">Job</label>
                <button
                    type="button"
                    className="dropdown-trigger"
                    onClick={() => setIsJobDropdownOpen(!isJobDropdownOpen)}
                    disabled={!selectedRepo || selectedRepo.jobs.length === 0}
                >
                    <span className="truncate">
                        {currentJob ? `${currentJob} (${selectedRepo?.jobs.find(j => j.id === currentJob)?.runs.length || 0} runs)` : 'Select a job'}
                    </span>
                    <ChevronDown className={`dropdown-icon ${isJobDropdownOpen ? 'rotate-180' : ''}`}/>
                </button>

                {isJobDropdownOpen && selectedRepo && (
                    <div className="dropdown-menu">
                        {selectedRepo.jobs.map(job => (
                            <div
                                key={job.id}
                                className={`dropdown-item ${currentJob === job.id ? 'dropdown-item-active' : ''}`}
                                onClick={() => {
                                    onJobChange(job.id);
                                    onRunChange([]);
                                    setIsJobDropdownOpen(false);
                                }}
                            >
                                {job.id} ({job.runs.length} runs)
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {/* Run Multi-Selector */}
            <div className="selector-group" ref={runRef}>
                <label className="selector-label">Runs</label>
                <button
                    type="button"
                    className="dropdown-trigger"
                    onClick={() => setIsRunDropdownOpen(!isRunDropdownOpen)}
                    disabled={!selectedJob || selectedJob.runs.length === 0}
                >
                    <span className="truncate">
                        {currentRuns.length === 0 ? 'Select run(s)' : currentRuns.length <= 2 ? currentRuns.join(', ') : `${currentRuns.length} runs selected`}
                    </span>
                    <ChevronDown className={`dropdown-icon ${isRunDropdownOpen ? 'rotate-180' : ''}`}/>
                </button>

                {isRunDropdownOpen && selectedJob && (
                    <div className="dropdown-menu">
                        {selectedJob.runs.map(run => (
                            <label key={run.id} className="dropdown-item gap-3">
                                <input
                                    type="checkbox"
                                    className="w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500"
                                    checked={currentRuns?.includes(run.id)}
                                    onChange={(e) => {
                                        const runs = currentRuns || [];
                                        if (e.target.checked) {
                                            onRunChange([...runs, run.id]);
                                            setIsAdded(true);
                                            if (!selectedRunId) setSelectedRunId(run.id);
                                        } else {
                                            const remaining = currentRuns.filter(id => id != run.id);
                                            onRunChange(remaining);
                                            setSummary(prev => prev.filter(item => item.run_id != run.id));
                                            setAllTests(prev => prev.filter(item => item.run_id != run.id));
                                            setAllFiles(prev => prev.filter(item => item.run_id != run.id));
                                            if (run.id === selectedRunId) {
                                                setSelectedRunId(remaining.length > 0 ? remaining[0] : null);
                                            }
                                        }
                                    }}
                                />
                                <div className="flex flex-col">
                                    <div className="flex items-center gap-2">
                                        <span className="font-semibold">Run #{run.id}</span>
                                        {run.tests_total != null ? (
                                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-100 text-green-700">
                                                {run.tests_total - (run.tests_skipped || 0)} ran
                                                {run.tests_skipped > 0 && `, ${run.tests_skipped} skipped`}
                                            </span>
                                        ) : (
                                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-100 text-yellow-700">
                                                no data
                                            </span>
                                        )}
                                    </div>
                                    <span className="text-[10px] text-gray-400">
                                        {new Date(run.created).toLocaleString()}
                                        {run.repo_run_id && <span className="ml-1 text-gray-500">(GH: {run.repo_run_id})</span>}
                                    </span>
                                </div>
                            </label>
                        ))}
                    </div>
                )}
            </div>

            <button className="btn-refresh" onClick={onRefresh}>
                <RefreshCw className="w-4 h-4"/>
                Refresh
            </button>

            <div className="mt-3 border-t pt-3 border-gray-100" ref={userRepoRef}>
                <p className="text-xs text-gray-500 mb-2 leading-relaxed">
                    Want to integrate <b>ezmon</b>? Select a repo to generate a workflow file.
                </p>
                <div className="relative">
                    <button
                        type="button"
                        className="dropdown-trigger mb-2 w-full"
                        disabled={!userOtherRepos || isGenerating}
                        onClick={() => setIsFutureRepoDropdownOpen(!isFutureRepoDropdownOpen)}
                    >
                        <span className="truncate">
                            {!userOtherRepos ? (
                                <span className="flex items-center gap-2">
                                    <Loader2 className="w-4 h-4 animate-spin shrink-0"/>Retrieving...
                                </span>
                            ) : (
                                selectedOtherRepo ? selectedOtherRepo.name : 'Select a repository'
                            )}
                        </span>
                        <ChevronDown className={`dropdown-icon ${isFutureRepoDropdownOpen ? 'rotate-180' : ''}`}/>
                    </button>

                    {isFutureRepoDropdownOpen && (
                        <div className="dropdown-menu max-h-48 overflow-y-auto">
                            {userOtherRepos?.map(otherRepo => (
                                <div
                                    key={otherRepo.id}
                                    className={`dropdown-item ${selectedOtherRepo && selectedOtherRepo.id === otherRepo.id ? 'dropdown-item-active' : ''}`}
                                    onClick={() => handleOtherRepoSelect(otherRepo)}
                                >
                                    {otherRepo.name}
                                </div>
                            ))}
                        </div>
                    )}
                </div>

                {isLoadingWorkflows && (
                    <div className="flex items-center gap-2 text-xs text-gray-400 py-1">
                        <Loader2 className="w-3 h-3 animate-spin"/> Checking workflows...
                    </div>
                )}

                {!isLoadingWorkflows && workflowFiles.length > 1 && (
                    <div className="mt-2 relative" ref={workflowRef}>
                        <label className="text-[10px] uppercase font-bold text-gray-400 mb-1 block">Select Workflow File</label>
                        <button
                            type="button"
                            className="dropdown-trigger"
                            onClick={() => setIsWorkflowDropdownOpen(!isWorkflowDropdownOpen)}
                        >
                             <span className="truncate flex items-center gap-2">
                                 <FileCode className="w-3 h-3 text-gray-500"/>
                                 {selectedWorkflow ? selectedWorkflow.name : 'Select workflow file'}
                             </span>
                            <ChevronDown className={`dropdown-icon ${isWorkflowDropdownOpen ? 'rotate-180' : ''}`}/>
                        </button>

                        {isWorkflowDropdownOpen && (
                            <div className="dropdown-menu absolute left-0 top-full mt-1 w-full z-50 max-h-48 overflow-y-auto shadow-lg border border-gray-100 rounded-md bg-white">
                                {workflowFiles.map((wf, idx) => (
                                    <div
                                        key={idx}
                                        className={`dropdown-item ${selectedWorkflow && selectedWorkflow.name === wf.name ? 'dropdown-item-active' : ''}`}
                                        onClick={() => handleWorkflowFileSelection(wf)}
                                    >
                                        {wf.name}
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                )}

                {!isLoadingWorkflows && selectedOtherRepo && (
                    <div className="mt-2 text-xs">
                        {workflowStatus === 'no_workflows' && (
                            <div className="flex items-start gap-2 text-amber-600 bg-amber-50 p-2 rounded">
                                <AlertCircle className="w-4 h-4 shrink-0 mt-0.5"/>
                                <span>No workflow files found in this repository.</span>
                            </div>
                        )}
                        {selectedWorkflow && !selectedWorkflow.uses_pytest && (
                            <div className="flex items-start gap-2 text-red-600 bg-red-50 p-2 rounded">
                                <AlertCircle className="w-4 h-4 shrink-0 mt-0.5"/>
                                <span>
                                    <b>{selectedWorkflow.name}</b> does not appear to use <code className="bg-red-100 px-1 rounded">pytest</code>.
                                </span>
                            </div>
                        )}
                    </div>
                )}

                <button
                    type="button"
                    disabled={!selectedOtherRepo || isGenerating || !selectedWorkflow?.uses_pytest}
                    className={`mt-3 w-full flex items-center justify-center gap-2 rounded-xl text-white font-medium text-sm px-4 py-2.5 transition-all
                        ${!selectedOtherRepo || isGenerating || !selectedWorkflow?.uses_pytest
                        ? "bg-gray-400 cursor-not-allowed opacity-70"
                        : "bg-gradient-to-br from-green-400 to-blue-600 hover:scale-[1.02] shadow-md"
                    }`}
                    onClick={handleGenerateClick}
                >
                    {isGenerating ? (
                        <><Loader2 className="w-4 h-4 animate-spin"/>Updating...</>
                    ) : (
                        "Update Workflow File With AI"
                    )}
                </button>
            </div>
        </div>
    );
}

export default SelectorBar;