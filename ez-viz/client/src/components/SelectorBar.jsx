import {RefreshCw, ChevronDown, Loader2} from "lucide-react";
import React, {useState, useRef, useEffect} from "react";

function SelectorBar({repos, currentRepo, currentJob, currentRuns, selectedRepo, selectedJob, onRepoChange, onJobChange, onRunChange, onRefresh, setIsAdded, setSummary, setAllTests, setAllFiles, selectedRunId, setSelectedRunId, userOtherRepos, generateWorkflowFile}) {
    const [isRepoDropdownOpen, setIsRepoDropdownOpen] = useState(false);
    const [isJobDropdownOpen, setIsJobDropdownOpen] = useState(false);
    const [isRunDropdownOpen, setIsRunDropdownOpen] = useState(false);
    const [isFutureRepoDropdownOpen, setIsFutureRepoDropdownOpen] = useState(false);
    const [selectedOtherRepo, setSelectedOtherRepo] = useState(null);
    const [isGenerating, setIsGenerating] = useState(false);

    const repoRef = useRef(null);
    const jobRef = useRef(null);
    const runRef = useRef(null);
    const userRepoRef = useRef(null);

    useEffect(() => {
        function handleClickOutside(event) {
            if (repoRef.current && !repoRef.current.contains(event.target)) setIsRepoDropdownOpen(false);
            if (jobRef.current && !jobRef.current.contains(event.target)) setIsJobDropdownOpen(false);
            if (runRef.current && !runRef.current.contains(event.target)) setIsRunDropdownOpen(false);
            if (userRepoRef.current && !userRepoRef.current.contains(event.target)) setIsFutureRepoDropdownOpen(false);
        }

        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    const handleGenerateClick = async () => {
        if (!selectedOtherRepo) return;
        setIsGenerating(true);
        await generateWorkflowFile(selectedOtherRepo);
        setIsGenerating(false);
    };

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
                                    <span className="font-semibold">{run.id}</span>
                                    <span className="text-[10px] text-gray-400">
                                        {new Date(run.created).toLocaleString()}
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

            <div className={"mt-3"} ref={userRepoRef}>
                <p className="text-xs text-gray-500 mb-2 leading-relaxed">
                    Want to integrate <b>ezmon</b>? Select a repo to generate a workflow file.
                </p>
                <button
                    type="button"
                    className="dropdown-trigger"
                    disabled={!userOtherRepos}
                    onClick={() => setIsFutureRepoDropdownOpen(!isFutureRepoDropdownOpen)}
                >
                    {!userOtherRepos ? <><Loader2 className="w-4 h-4 animate-spin"/>Retrieving...</> : selectedOtherRepo ? `${selectedOtherRepo.name}` : 'Select a repository'}
                    <ChevronDown className={`dropdown-icon ${isFutureRepoDropdownOpen ? 'rotate-180' : ''}`}/>
                </button>
                {isFutureRepoDropdownOpen && (
                    <div>
                        {userOtherRepos?.map(otherRepo => (
                            <div
                                key={otherRepo.id}
                                className={`dropdown-item ${selectedOtherRepo && selectedOtherRepo.id === otherRepo.id ? 'dropdown-item-active' : ''}`}
                                onClick={() => {
                                    setSelectedOtherRepo(otherRepo)
                                    setIsFutureRepoDropdownOpen(false);
                                }}
                            >
                                {otherRepo.name}
                            </div>
                        ))}
                    </div>
                )}
                <button
                    type="button"
                    disabled={!selectedOtherRepo || isGenerating}
                    className={`mt-3 w-full flex items-center justify-center gap-2 rounded-xl text-white font-medium text-sm px-4 py-2.5 transition-all
                        ${!selectedOtherRepo || isGenerating
                        ? "bg-gray-400 cursor-not-allowed"
                        : "bg-gradient-to-br from-green-400 to-blue-600 hover:scale-[1.02] shadow-md"
                    }`}
                    onClick={handleGenerateClick}
                >
                    {isGenerating ? (
                        <><Loader2 className="w-4 h-4 animate-spin"/>Generating...</>
                    ) : (
                        "Generate Workflow File"
                    )}
                </button>
            </div>
        </div>
    );
}

export default SelectorBar;