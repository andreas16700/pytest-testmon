import { RefreshCw, ChevronDown } from "lucide-react";
import React, { useState, useRef, useEffect } from "react";

function SelectorBar({ repos, currentRepo, currentJob, currentRuns, selectedRepo, selectedJob, onRepoChange, onJobChange, onRunChange, onRefresh, setIsAdded, setSummary, setAllTests, setAllFiles, selectedRunId, setSelectedRunId }) {
    const [isRepoDropdownOpen, setIsRepoDropdownOpen] = useState(false);
    const [isJobDropdownOpen, setIsJobDropdownOpen] = useState(false);
    const [isRunDropdownOpen, setIsRunDropdownOpen] = useState(false);
   
    const repoRef = useRef(null);
    const jobRef = useRef(null);
    const runRef = useRef(null);

    useEffect(() => {
        function handleClickOutside(event) {
            if (repoRef.current && !repoRef.current.contains(event.target)) setIsRepoDropdownOpen(false);
            if (jobRef.current && !jobRef.current.contains(event.target)) setIsJobDropdownOpen(false);
            if (runRef.current && !runRef.current.contains(event.target)) setIsRunDropdownOpen(false);
        }
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

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
                    <ChevronDown className={`dropdown-icon ${isRepoDropdownOpen ? 'rotate-180' : ''}`} />
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
                    <ChevronDown className={`dropdown-icon ${isJobDropdownOpen ? 'rotate-180' : ''}`} />
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
                    <ChevronDown className={`dropdown-icon ${isRunDropdownOpen ? 'rotate-180' : ''}`} />
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

            {/* Action Button */}
            <button className="btn-refresh" onClick={onRefresh}>
                <RefreshCw className="w-4 h-4" />
                Refresh
            </button>
        </div>
    );
}

export default SelectorBar;