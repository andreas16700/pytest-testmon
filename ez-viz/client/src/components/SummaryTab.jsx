import React, { useState, useMemo } from "react";
import EnvItem from "./EnvItem.jsx";
import StatCard from "./StatCard.jsx";
import { Doughnut } from "react-chartjs-2";
import {
    Chart as ChartJS,
    ArcElement,
    Tooltip,
    Legend,
} from "chart.js";
import { CheckCircle2 } from "lucide-react";

ChartJS.register(ArcElement, Tooltip, Legend);

function SummaryTab({ summary, allTests, currentRepo, currentJob, currentRuns, selectedRunId, setSelectedRunId }) {
    const activeRunData = useMemo(() => {
        return allTests.find(run => run.run_id === selectedRunId) || { tests: [] };
    }, [allTests, selectedRunId]);

    const activeSummary = useMemo(() => {
        return summary.find(s => s.run_id === selectedRunId) || {};
    }, [summary, selectedRunId]);

    const currentTests = activeRunData.tests || [];
    const passed = currentTests.filter(t => !t.failed).length;
    const failed = currentTests.filter(t => t.failed).length;
    const ran = currentTests.filter(t => t.forced === 0).length;

    const totalTests = activeSummary.test_count || 0;
    const skipped = totalTests - ran;

    const [runtimeSpent, runtimeSaved] = currentTests.reduce(
        (acc, test) => {
            if (test.forced === 0) {
                acc[0] += test.duration;
            } else {
                acc[1] += test.duration;
            }
            return acc;
        },
        [0, 0]
    );

    const testsChartData = {
        labels: ['Tests Executed', 'Tests Skipped'],
        datasets: [{
            data: [ran, skipped],
            backgroundColor: ['#3B82F6', '#10B981'],
            borderColor: ['#2563EB', '#059669'],
            borderWidth: 2,
        }],
    };

    const runtimeChartData = {
        labels: ['Runtime Spent', 'Runtime Saved'],
        datasets: [{
            data: [runtimeSpent, runtimeSaved],
            backgroundColor: ['#F59E0B', '#10B981'],
            borderColor: ['#D97706', '#059669'],
            borderWidth: 2,
        }],
    };

    const chartOptions = {
        responsive: true,
        maintainAspectRatio: true,
        plugins: {
            legend: {
                position: 'bottom',
            },
        },
    };

    return (
        <div className="animate-fadeIn">
            <div className="max-w-4xl mx-auto p-6">
                <div>
                    {currentRuns.map((element, index) => {
                        const isSelected = selectedRunId === element;
                        return (
                            <label
                                key={index}
                                htmlFor={`run-${index}`}
                                className={`
                                    group relative flex flex-col p-5 cursor-pointer mb-3
                                    rounded-xl border transition-all duration-200 ease-in-out
                                    ${isSelected
                                    ? 'border-indigo-600 bg-indigo-50/50 shadow-md ring-1 ring-indigo-600'
                                    : 'border-gray-200 bg-white hover:border-indigo-300 hover:shadow-sm'
                                }
                                `}
                            >
                                <input
                                    type="radio"
                                    id={`run-${index}`}
                                    name="testRun"
                                    value={element}
                                    checked={isSelected}
                                    onChange={(e) => setSelectedRunId(e.target.value)}
                                    className="sr-only"
                                />

                                <div className="flex justify-between items-start mb-2">
                                    <div className="flex items-center gap-2">
                                         <span className={`text-xs font-bold uppercase tracking-wider ${isSelected ? 'text-indigo-700' : 'text-gray-400'}`}>
                                            Run ID
                                         </span>
                                    </div>
                                    <div className={`transition-opacity duration-200 ${isSelected ? 'opacity-100' : 'opacity-0'}`}>
                                        <CheckCircle2 className="w-5 h-5 text-indigo-600" />
                                    </div>
                                </div>

                                <div>
                                    <span className={`text-xl font-mono font-bold tracking-tight ${
                                        isSelected ? 'text-indigo-900' : 'text-gray-700 group-hover:text-gray-900'
                                    }`}>
                                        {element}
                                    </span>
                                </div>
                            </label>
                        );
                    })}
                </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-5 mb-8">
                <StatCard
                    title="Tests"
                    value={activeSummary.test_count || 0}
                    label={`${passed} passed, ${failed} failed, ${skipped} skipped`}
                />
                <StatCard
                    title="Files Tracked"
                    value={activeSummary.file_count || 0}
                    label="monitored for changes"
                />
                <StatCard
                    title="Repository"
                    value={currentRepo?.split('/').pop() || 'N/A'}
                    label={currentJob}
                    smallValue
                />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                <div className="bg-white p-5 rounded-lg shadow">
                    <h3 className="text-gray-700 mb-4 text-lg font-semibold text-center">Test Distribution</h3>
                    <div className="flex justify-center" style={{maxWidth: '300px', margin: '0 auto'}}>
                        <Doughnut data={testsChartData} options={chartOptions}/>
                    </div>
                </div>
                <div className="bg-white p-5 rounded-lg shadow">
                    <h3 className="text-gray-700 mb-4 text-lg font-semibold text-center">Runtime Distribution</h3>
                    <div className="flex justify-center" style={{ maxWidth: '300px', margin: '0 auto' }}>
                        <Doughnut data={runtimeChartData} options={chartOptions} />
                    </div>
                </div>
            </div>

            <div className="bg-gray-50 p-5 rounded-lg border-l-4 border-indigo-500 mt-5">
                <h3 className="text-gray-700 mb-4 text-lg font-semibold">Environment Information</h3>
                <EnvItem label="Environment" value={activeSummary.environment?.name || 'N/A'}/>
                <EnvItem label="Python Version" value={activeSummary.environment?.python_version || 'N/A'}/>
                <EnvItem label="Packages" value={activeSummary.environment?.packages || 'N/A'}/>
            </div>
        </div>
    );
}

export default SummaryTab;