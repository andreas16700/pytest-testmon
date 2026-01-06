import React, { useState, useMemo } from "react";
import EnvItem from "./EnvItem.jsx";
import StatCard from "./StatCard.jsx";
import { Doughnut, Bar } from "react-chartjs-2";
import {
    Chart as ChartJS,
    ArcElement,
    Tooltip,
    Legend,
    CategoryScale,
    LinearScale,
    BarElement,
} from "chart.js";
import { CheckCircle2, ArrowRightLeft, LayoutDashboard, GitCompare } from "lucide-react";

ChartJS.register(ArcElement, Tooltip, Legend, CategoryScale, LinearScale, BarElement);

function SummaryTab({ summary, allTests, currentRepo, currentJob, currentRuns, selectedRunId, setSelectedRunId }) {
    const [activeTab, setActiveTab] = useState('single');
    const [compareRunA, setCompareRunA] = useState(selectedRunId);
    const [compareRunB, setCompareRunB] = useState(currentRuns.find(r => r !== selectedRunId) || currentRuns[0]);
    const getRunData = (runId) => {
        const runTests = allTests.find(run => run.run_id == runId) || { tests: [] };
        const runSummary = summary.find(s => s.run_id == runId) || {};

        const currentTests = runTests.tests || [];
        const failed = currentTests.filter(t => t.failed).length;
        const ran = currentTests.filter(t => t.forced === 0 || t.forced===1) .length; 
        const totalTests = runSummary.test_count || 0;
        const skipped = totalTests - ran;
        const passed= totalTests - skipped - failed
        const [runtimeSpent, runtimeSaved] = currentTests.reduce(
            (acc, test) => {
                if (test.forced !== null ) {
                    acc[0] += test.duration;
                } else {
                    acc[1] += test.duration;
                }
                return acc;
            },
            [0, 0]
        );

        return {
            id: runId,
            summary: runSummary,
            tests: currentTests,
            stats: { passed, failed, ran, skipped, totalTests, runtimeSpent, runtimeSaved }
        };
    };

    // Data for comparison view
    const isComparisonView = activeTab === 'compare';
    const runAData = useMemo(() => isComparisonView ? getRunData(compareRunA) : null, [compareRunA, allTests, summary, isComparisonView]);
    const runBData = useMemo(() => isComparisonView ? getRunData(compareRunB) : null, [compareRunB, allTests, summary, isComparisonView]);

    // Data for single view
    const primaryRun = useMemo(() => getRunData(selectedRunId), [selectedRunId, allTests, summary]);
        const { passed, failed, skipped, totalTests } = primaryRun.stats;

    const passRatio   = totalTests ? (passed  / totalTests) * 100 : 0;
    const skipRatio   = totalTests ? (skipped / totalTests) * 100 : 0;
    const failRatio   = totalTests ? (failed  / totalTests) * 100 : 0;


    const handleTabChange = (tab) => {
        setActiveTab(tab);
        if (tab === 'compare') {
            setCompareRunA(selectedRunId);
            const other = currentRuns.find(r => r !== selectedRunId);
            if (other) setCompareRunB(other);
        }
    };

    // --- Charts for Single View ---
    const testsChartData = {
        labels: ["Tests Executed", "Tests Skipped"],
        datasets: [
            {
                data: [primaryRun.stats.ran, primaryRun.stats.skipped],
                backgroundColor: ["#10B981", "#eab308"],
                borderColor: ["#059669", "#ca8a04"],
                borderWidth: 2,
            },
        ],
    };

    const runtimeChartData = {
        labels: ['Runtime Spent', 'Runtime Saved'],
        datasets: [{
            data: [primaryRun.stats.runtimeSpent, primaryRun.stats.runtimeSaved],
            backgroundColor: ['#ef4444', '#3B82F6'],
            borderColor: ['#dc2626', '#2563EB'],
            borderWidth: 2,
        }],
    };

    // --- Charts for Comparison View ---
    const comparisonChartData = (runAData && runBData) ? {
        labels: ['Total Tests', 'Executed', 'Skipped', 'Failed'],
        datasets: [
            {
                label: `Run ${runAData.id}`,
                data: [runAData.stats.totalTests, runAData.stats.ran, runAData.stats.skipped, runAData.stats.failed],
                backgroundColor: [
                    'rgba(59, 130, 246, 0.7)',  // Total - blue
                    'rgba(16, 185, 129, 0.7)',  // Executed - green
                    'rgba(234, 179, 8, 0.7)',   // Skipped - yellow
                    'rgba(239, 68, 68, 0.7)'    // Failed - red
                ],
                borderColor: [
                    'rgba(59, 130, 246, 1)',
                    'rgba(16, 185, 129, 1)',
                    'rgba(234, 179, 8, 1)',
                    'rgba(239, 68, 68, 1)'
                ],
                borderWidth: 1
            },
            {
                label: `Run ${runBData.id}`,
                data: [runBData.stats.totalTests, runBData.stats.ran, runBData.stats.skipped, runBData.stats.failed],
                backgroundColor: [
                    'rgba(59, 130, 246, 0.5)',  // Total - blue (lighter)
                    'rgba(16, 185, 129, 0.5)',  // Executed - green (lighter)
                    'rgba(234, 179, 8, 0.5)',   // Skipped - yellow (lighter)
                    'rgba(239, 68, 68, 0.5)'    // Failed - red (lighter)
                ],
                borderColor: [
                    'rgba(59, 130, 246, 1)',
                    'rgba(16, 185, 129, 1)',
                    'rgba(234, 179, 8, 1)',
                    'rgba(239, 68, 68, 1)'
                ],
                borderWidth: 1
            },
        ],
    } : null;

    const chartOptions = {
        responsive: true,
        maintainAspectRatio: true,
        plugins: {
            legend: { position: 'bottom' },
        },
    };

    return (
        <div className="summary-container">
            <div className="summary-inner">
                <div className="flex justify-center mb-6 border-b border-gray-200">
                    <div className="flex space-x-8">
                        <button
                            onClick={() => handleTabChange('single')}
                            className={`pb-4 px-4 flex items-center gap-2 font-medium text-sm transition-colors border-b-2 ${
                                activeTab === 'single'
                                    ? 'border-indigo-600 text-indigo-600'
                                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                            }`}
                        >
                            <LayoutDashboard className="w-4 h-4" />
                            Single Run Overview
                        </button>
                        <button
                            onClick={() => handleTabChange('compare')}
                            className={`pb-4 px-4 flex items-center gap-2 font-medium text-sm transition-colors border-b-2 ${
                                activeTab === 'compare'
                                    ? 'border-indigo-600 text-indigo-600'
                                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                            }`}
                        >
                            <GitCompare className="w-4 h-4" />
                            Compare Runs
                        </button>
                    </div>
                </div>

                {activeTab === 'compare' ? (
                    <div className="comparison-card animate-fadeIn">

                        <div className="flex flex-row items-end justify-center gap-6 mb-8 p-4 bg-gray-50 rounded-lg border border-gray-100">

                            {/* Base Run (A) */}
                            <div className="flex flex-col w-64">
                                <label className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
                                    Base Run (A)
                                </label>
                                <select
                                    value={compareRunA}
                                    onChange={(e) => setCompareRunA(e.target.value)}
                                    className="block w-full rounded-md border-gray-300 shadow-sm focus:border-indigo-500 focus:ring-indigo-500 sm:text-sm p-2.5 bg-white border"
                                >
                                    {currentRuns.map(r => (
                                        <option key={r} value={r} disabled={r === compareRunB}>{r}</option>
                                    ))}
                                </select>
                            </div>

                            {/* Center Arrow */}
                            <div className="flex items-center justify-center pb-3 text-gray-400">
                                <ArrowRightLeft className="w-6 h-6" />
                            </div>

                            {/* Comparison Run (B) */}
                            <div className="flex flex-col w-64">
                                <label className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
                                    Comparison Run (B)
                                </label>
                                <select
                                    value={compareRunB}
                                    onChange={(e) => setCompareRunB(e.target.value)}
                                    className="block w-full rounded-md border-gray-300 shadow-sm focus:border-indigo-500 focus:ring-indigo-500 sm:text-sm p-2.5 bg-white border"
                                >
                                    {currentRuns.map(r => (
                                        <option key={r} value={r} disabled={r === compareRunA}>{r}</option>
                                    ))}
                                </select>
                            </div>
                        </div>

                        {/* Comparison Content */}
                        <div className="comparison-content">

                            <div className="comparison-section">
                                <h4 className="comparison-section-title">Statistics Delta</h4>
                                <table className="comparison-table">
                                    <thead className="comparison-table-head">
                                    <tr>
                                        <th className="comparison-table-header-cell">Metric</th>
                                        <th className="comparison-table-header-cell comparison-table-header-run-a">Run {runAData.id}</th>
                                        <th className="comparison-table-header-cell comparison-table-header-run-b">Run {runBData.id}</th>
                                        <th className="comparison-table-header-cell">Diff</th>
                                    </tr>
                                    </thead>
                                    <tbody className="comparison-table-body">
                                    {[
                                        { label: 'Total Tests', key: 'totalTests' },
                                        { label: 'Executed', key: 'ran' },
                                        { label: 'Skipped', key: 'skipped' },
                                        { label: 'Failed', key: 'failed', reverseColor: true },
                                        { label: 'Runtime (s)', key: 'runtimeSpent', format: (v) => v.toFixed(2) },
                                    ].map((row) => {
                                        const v1 = runAData.stats[row.key];
                                        const v2 = runBData.stats[row.key];
                                        const diff = v1 - v2;
                                        const format = row.format || ((v) => v);
                                        const colorClass = diff === 0 ? 'text-gray-400' : (row.reverseColor ? (diff < 0 ? 'text-green-600' : 'text-red-600') : (diff > 0 ? 'text-green-600' : 'text-red-600'));

                                        return (
                                            <tr key={row.key}>
                                                <td className="comparison-table-cell">{row.label}</td>
                                                <td className="comparison-table-cell-mono">{format(v1)}</td>
                                                <td className="comparison-table-cell-mono">{format(v2)}</td>
                                                <td className={`comparison-table-cell-diff ${colorClass}`}>
                                                    {diff > 0 ? '+' : ''}{format(diff)}
                                                </td>
                                            </tr>
                                        );
                                    })}
                                    </tbody>
                                </table>
                            </div>

                            {/* Comparison Chart */}
                            <div className="comparison-section">
                                <h4 className="comparison-section-title">Visual Comparison</h4>
                                <div className="comparison-chart-container" style={{ height: '400px', maxWidth: '900px', margin: '0 auto' }}>
                                    <Bar 
                                        data={comparisonChartData} 
                                        options={{
                                            ...chartOptions, 
                                            maintainAspectRatio: false,
                                            scales: {
                                                y: {
                                                    beginAtZero: true,
                                                    ticks: {
                                                        font: { size: 12 }
                                                    }
                                                },
                                                x: {
                                                    ticks: {
                                                        font: { size: 12 }
                                                    }
                                                }
                                            },
                                            plugins: {
                                                legend: { 
                                                    position: 'top',
                                                    labels: {
                                                        font: { size: 13 },
                                                        padding: 15
                                                    }
                                                },
                                                tooltip: {
                                                    backgroundColor: 'rgba(0, 0, 0, 0.8)',
                                                    padding: 12,
                                                    titleFont: { size: 14 },
                                                    bodyFont: { size: 13 }
                                                }
                                            }
                                        }} 
                                    />
                                </div>
                            </div>
                        </div>
                    </div>
                ) : (
                    <div className="animate-fadeIn">
                        <div className="run-selection-section">
                            <h3 className="run-selection-title text-center">Select Run</h3>
                            <div className="run-selection-grid justify-center">
                                {currentRuns.map((runId, index) => {
                                    const isSelected = selectedRunId === runId;
                                    return (
                                        <div
                                            key={index}
                                           s className={`run-card ${isSelected ? 'run-card-selected' : 'run-card-unselected'}`}
                                            onClick={() => setSelectedRunId(runId)}
                                        >
                                            <div className="run-card-header">
                                                <span className="run-card-label">RUN ID</span>
                                                {isSelected && <CheckCircle2 className="w-4 h-4 text-indigo-600" />}
                                            </div>
                                            <div className="run-card-id" title={runId}>
                                                {runId}
                                            </div>
                                        </div>
                                    );
                                })}
                            </div>
                        </div>

                        <div className="stats-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '1rem', maxWidth: '900px', margin: '0 auto' }}>
                            <StatCard
                                title="Tests"
                                value={primaryRun.summary.test_count || 0}
                                label={`${primaryRun.stats.passed} passed, ${primaryRun.stats.failed} failed, ${primaryRun.stats.skipped} skipped`}
                            />
                            <StatCard
                                title="Files Tracked"
                                value={primaryRun.summary.file_count || 0}
                                label="monitored for changes"
                            />
                          
                               <StatCard
                                title="Created"
                                value={primaryRun.summary.create_date || 'N/A'}
                                smallValue
                            />
                        </div>

                        <div className="charts-grid">
                            <div className="chart-card">
                                <h3 className="chart-title">
                                    Test Distribution
                                </h3>
                                <div className="chart-wrapper" style={{ maxWidth: "300px", margin: "0 auto" }}>
                                    <Doughnut data={testsChartData} options={chartOptions} />
                                </div>
                            </div>
                            <div className="chart-card">
                                <h3 className="chart-title">
                                    Runtime Distribution
                                </h3>
                                <div className="chart-wrapper" style={{ maxWidth: "300px", margin: "0 auto" }}>
                                    <Doughnut data={runtimeChartData} options={chartOptions} />
                                </div>
                            </div>
                        </div>

                                              {/* Pass / Skip / Fail Ratios */}
                        <div className="mt-10 px-4">
                            <h3 className="text-sm font-semibold text-gray-600 mb-3 text-center">
                                Pass / Skip / Fail Ratios
                            </h3>

                            {/* Bar visualization */}
                            <div className="w-full max-w-xl mx-auto h-4 rounded-full overflow-hidden bg-gray-100 shadow-inner">
                                <div
                                    className="h-full"
                                    style={{
                                        width: `${passRatio}%`,
                                        display: "inline-block",
                                        backgroundColor: "#22c55e" // passed (green)
                                    }}
                                />
                                <div
                                    className="h-full"
                                    style={{
                                        width: `${skipRatio}%`,
                                        display: "inline-block",
                                        backgroundColor: "#eab308" // skipped (yellow)
                                    }}
                                />
                                <div
                                    className="h-full"
                                    style={{
                                        width: `${failRatio}%`,
                                        display: "inline-block",
                                        backgroundColor: "#ef4444" // failed (red)
                                    }}
                                />
                            </div>

                            {/* Text summary */}
                            <div className="mt-3 flex justify-center gap-6 text-xs text-gray-700">
                                <div className="flex items-center gap-1">
                                    <span className="inline-block w-3 h-3 rounded-sm" style={{ backgroundColor: "#22c55e" }} />
                                    <span>Passed: {passed} ({passRatio.toFixed(1)}%)</span>
                                </div>
                                <div className="flex items-center gap-1">
                                    <span className="inline-block w-3 h-3 rounded-sm" style={{ backgroundColor: "#eab308" }} />
                                    <span>Skipped: {skipped} ({skipRatio.toFixed(1)}%)</span>
                                </div>
                                <div className="flex items-center gap-1">
                                    <span className="inline-block w-3 h-3 rounded-sm" style={{ backgroundColor: "#ef4444" }} />
                                    <span>Failed: {failed} ({failRatio.toFixed(1)}%)</span>
                                </div>
                            </div>
                        </div>

                    </div>
                )}
            </div>
        </div>
    );
}

export default SummaryTab;