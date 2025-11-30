import {RefreshCw} from "lucide-react";
import React, {useState, useRef, useEffect} from "react";

function SelectorBar({ repos, currentRepo, currentJob, currentRuns, selectedRepo, selectedJob, onRepoChange, onJobChange, onRunChange, onRefresh, setIsAdded, setSummary, setAllTests, setAllFiles, selectedRunId, setSelectedRunId }) {
    const [isRepoDropdownOpen, setIsRepoDropdownOpen] = useState(false);
    const [isJobDropdownOpen, setIsJobDropdownOpen] = useState(false);
    const [isRunDropdownOpen, setIsRunDropdownOpen] = useState(false);

    const repoRef = useRef(null);
    const jobRef = useRef(null);
    const runRef = useRef(null);

    useEffect(() => {
        function handleClickOutside(event) {
            if (repoRef.current && !repoRef.current.contains(event.target)) {
                setIsRepoDropdownOpen(false);
            }
            if (jobRef.current && !jobRef.current.contains(event.target)) {
                setIsJobDropdownOpen(false);
            }
            if (runRef.current && !runRef.current.contains(event.target)) {
                setIsRunDropdownOpen(false);
            }
        }

        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    return (
        <div className="bg-gray-50 p-5 border-b-2 border-gray-200 flex gap-5 items-end flex-wrap">
            <div className="flex-1 min-w-[250px]" ref={repoRef}>
                <label className="block font-semibold text-gray-600 mb-2 text-sm uppercase tracking-wide">
                    Repository
                </label>

                <div className="relative">
                    <button
                        type="button"
                        className="w-full p-3 text-base border-2 border-gray-300 rounded-lg bg-white cursor-pointer transition-all hover:border-indigo-500 focus:outline-none focus:border-indigo-500 focus:ring-4 focus:ring-indigo-100 disabled:opacity-50 disabled:cursor-not-allowed text-left flex items-center justify-between"
                        onClick={() => setIsRepoDropdownOpen(!isRepoDropdownOpen)}
                        disabled={repos.length === 0}
                    >
                        <span className="truncate">
                            {currentRepo ?
                                `${currentRepo} (${repos.find(r => r.id === currentRepo)?.jobs.length || 0} jobs)` :
                                'Select a repository'}
                        </span>
                        <svg
                            className={`w-5 h-5 transition-transform flex-shrink-0 ml-2 ${isRepoDropdownOpen ? 'rotate-180' : ''}`}
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                        >
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                        </svg>
                    </button>

                    {isRepoDropdownOpen && repos.length > 0 && (
                        <div className="absolute z-10 w-full mt-1 bg-white border-2 border-gray-300 rounded-lg shadow-lg max-h-60 overflow-auto">
                            {repos.map(repo => (
                                <div
                                    key={repo.id}
                                    className={`p-3 hover:bg-indigo-50 cursor-pointer transition-colors ${currentRepo === repo.id ? 'bg-indigo-100' : ''}`}
                                    onClick={() => {
                                        onRepoChange(repo.id);
                                        onJobChange(null);
                                        onRunChange([]);
                                        setIsRepoDropdownOpen(false);
                                    }}
                                >
                                    <span className="text-base">{repo.name} ({repo.jobs.length} jobs)</span>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </div>

            <div className="flex-1 min-w-[250px]" ref={jobRef}>
                <label className="block font-semibold text-gray-600 mb-2 text-sm uppercase tracking-wide">
                    Job
                </label>

                <div className="relative">
                    <button
                        type="button"
                        className="w-full p-3 text-base border-2 border-gray-300 rounded-lg bg-white cursor-pointer transition-all hover:border-indigo-500 focus:outline-none focus:border-indigo-500 focus:ring-4 focus:ring-indigo-100 disabled:opacity-50 disabled:cursor-not-allowed text-left flex items-center justify-between"
                        onClick={() => setIsJobDropdownOpen(!isJobDropdownOpen)}
                        disabled={!selectedRepo}
                    >
                        <span className="truncate">
                            {currentJob ? `${currentJob} (${selectedRepo?.jobs.find(j => j.id === currentJob)?.runs.length || 0} runs)` : 'Select a job'}
                        </span>
                        <svg
                            className={`w-5 h-5 transition-transform flex-shrink-0 ml-2 ${isJobDropdownOpen ? 'rotate-180' : ''}`}
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                        >
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                        </svg>
                    </button>

                    {isJobDropdownOpen && selectedRepo && (
                        <div className="absolute z-10 w-full mt-1 bg-white border-2 border-gray-300 rounded-lg shadow-lg max-h-60 overflow-auto">
                            {selectedRepo.jobs.map(job => (
                                <div
                                    key={job.id}
                                    className={`p-3 hover:bg-indigo-50 cursor-pointer transition-colors ${currentJob === job.id ? 'bg-indigo-100' : ''}`}
                                    onClick={() => {
                                        onJobChange(job.id);
                                        onRunChange([]);
                                        setIsJobDropdownOpen(false);
                                    }}
                                >
                                    <span className="text-base">{job.id} ({job.runs.length} runs)</span>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </div>

            <div className="flex-1 min-w-[250px]" ref={runRef}>
                <label className="block font-semibold text-gray-600 mb-2 text-sm uppercase tracking-wide">
                    Run
                </label>
                <div className="relative">
                    <button
                        type="button"
                        className="w-full p-3 text-base border-2 border-gray-300 rounded-lg bg-white cursor-pointer transition-all hover:border-indigo-500 focus:outline-none focus:border-indigo-500 focus:ring-4 focus:ring-indigo-100 disabled:opacity-50 disabled:cursor-not-allowed text-left flex items-center justify-between"
                        onClick={() => setIsRunDropdownOpen(!isRunDropdownOpen)}
                        disabled={!selectedJob}
                    >
                        <span className="truncate">
                            {currentRuns.length === 0 ? 'Select run/s' : currentRuns.length <= 2 ? currentRuns.join(', ') : `${currentRuns.length} runs selected`}
                        </span>
                        <svg
                            className={`w-5 h-5 transition-transform ${isRunDropdownOpen ? 'rotate-180' : ''}`}
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                        >
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                        </svg>
                    </button>

                    {isRunDropdownOpen && selectedJob && (
                        <div className="absolute z-10 w-full mt-1 bg-white border-2 border-gray-300 rounded-lg shadow-lg max-h-60 overflow-auto">
                            {selectedJob.runs.map(run => (
                                <label
                                    key={run.id}
                                    className="flex items-center p-3 hover:bg-indigo-50 cursor-pointer transition-colors"
                                >
                                    <input
                                        type="checkbox"
                                        className="w-4 h-4 text-indigo-600 border-gray-300 rounded focus:ring-indigo-500 cursor-pointer"
                                        checked={currentRuns?.includes(run.id)}
                                        onChange={(e) => {
                                            const runs = currentRuns || [];
                                            if (e.target.checked) {
                                                onRunChange([...runs, run.id]);
                                                setIsAdded(true);
                                                if (!selectedRunId) {
                                                    setSelectedRunId(run.id);
                                                }
                                            } else {
                                                const remainingRuns = currentRuns.filter(id => id !== run.id);
                                                onRunChange(remainingRuns);
                                                setSummary(prev => prev.filter(item => item.run_id !== run.id));
                                                if (run.id === selectedRunId) {
                                                    if (remainingRuns.length > 0) {
                                                        setSelectedRunId(remainingRuns[0]);
                                                    } else {
                                                        setSelectedRunId(null);
                                                    }
                                                }
                                            }
                                        }}
                                    />
                                    <span className="ml-3 text-base">{run.id} (updated: {new Date(run.last_updated).toLocaleString()})</span>
                                </label>
                            ))}
                        </div>
                    )}
                </div>
            </div>

            <button
                className="px-6 py-3 bg-indigo-500 text-white rounded-lg cursor-pointer font-semibold transition-all hover:bg-indigo-600 hover:-translate-y-0.5 hover:shadow-lg flex items-center gap-2"
                onClick={onRefresh}
            >
                <RefreshCw size={20} />
                Refresh
            </button>
        </div>
    );
}

export default SelectorBar;