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
import { CheckCircle2, ArrowRightLeft, X } from "lucide-react";

ChartJS.register(ArcElement, Tooltip, Legend, CategoryScale, LinearScale, BarElement);

function SummaryTab({ summary, allTests, currentRepo, currentJob, currentRuns, selectedRunId, setSelectedRunId }) {

    const [isComparisonView, setIsComparisonView] = useState(false);
    const [compareRunA, setCompareRunA] = useState(selectedRunId);
    const [compareRunB, setCompareRunB] = useState(currentRuns.find(r => r !== selectedRunId) || currentRuns[0]);

    // --- Helper to get data for a specific run ---
    const getRunData = (runId) => {
        const runTests = allTests.find(run => run.run_id == runId) || { tests: [] };
        const runSummary = summary.find(s => s.run_id == runId) || {};
        
        const currentTests = runTests.tests || [];
        const passed = currentTests.filter(t => !t.failed).length;
        const failed = currentTests.filter(t => t.failed).length;
        const ran = currentTests.filter(t => t.forced === 0).length;
        const totalTests = runSummary.test_count || 0;
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

        return {
            id: runId,
            summary: runSummary,
            tests: currentTests,
            stats: { passed, failed, ran, skipped, totalTests, runtimeSpent, runtimeSaved }
        };
    };

    // Data for comparison view
    const runAData = useMemo(() => isComparisonView ? getRunData(compareRunA) : null, [compareRunA, allTests, summary, isComparisonView]);
    const runBData = useMemo(() => isComparisonView ? getRunData(compareRunB) : null, [compareRunB, allTests, summary, isComparisonView]);

    // Data for single view
    const primaryRun = useMemo(() => getRunData(selectedRunId), [selectedRunId, allTests, summary]);

    // --- Charts for Single View ---
    const testsChartData = {
        labels: ["Tests Executed", "Tests Skipped"],
        datasets: [
            {
                data: [primaryRun.stats.ran, primaryRun.stats.skipped],
                backgroundColor: ["#3B82F6", "#10B981"],
                borderColor: ["#2563EB", "#059669"],
                borderWidth: 2,
            },
        ],
    };

    const runtimeChartData = {
        labels: ['Runtime Spent', 'Runtime Saved'],
        datasets: [{
            data: [primaryRun.stats.runtimeSpent, primaryRun.stats.runtimeSaved],
            backgroundColor: ['#F59E0B', '#10B981'],
            borderColor: ['#D97706', '#059669'],
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
                backgroundColor: 'rgba(59, 130, 246, 0.7)',
            },
            {
                label: `Run ${runBData.id}`,
                data: [runBData.stats.totalTests, runBData.stats.ran, runBData.stats.skipped, runBData.stats.failed],
                backgroundColor: 'rgba(16, 185, 129, 0.7)',
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
                
                {/* Header / Mode Switch */}
                <div className="summary-header">
                     <button 
                        onClick={() => {
                            setIsComparisonView(!isComparisonView);
                            if (!isComparisonView) {
                                setCompareRunA(selectedRunId);
                                // Try to find a different run for B
                                const other = currentRuns.find(r => r !== selectedRunId);
                                if (other) setCompareRunB(other);
                            }
                        }}
                        className="mode-switch-button"
                     >
                        {isComparisonView ? (
                            <>
                                <X className="w-4 h-4" /> Exit Comparison
                            </>
                        ) : (
                            <>
                                <ArrowRightLeft className="w-4 h-4" /> Compare Runs
                            </>
                        )}
                     </button>
                </div>

                {isComparisonView ? (
                    // Comparison View
                    <div className="comparison-card">
                        {/* Selectors */}
                        <div className="comparison-selectors">
                             {/* Selector A */}
                             <div className="selector-wrapper">
                                <label className="selector-label">Base Run (A)</label>
                                <select 
                                    value={compareRunA} 
                                    onChange={(e) => setCompareRunA(e.target.value)}
                                    className="selector-dropdown"
                                >
                                    {currentRuns.map(r => (
                                        <option key={r} value={r} disabled={r === compareRunB}>{r}</option>
                                    ))}
                                </select>
                             </div>
                             {/* Selector B */}
                             <div className="selector-wrapper">
                                <label className="selector-label">Comparison Run (B)</label>
                                <select 
                                    value={compareRunB} 
                                    onChange={(e) => setCompareRunB(e.target.value)}
                                    className="selector-dropdown"
                                >
                                    {currentRuns.map(r => (
                                        <option key={r} value={r} disabled={r === compareRunA}>{r}</option>
                                    ))}
                                </select>
                             </div>
                        </div>

                        {/* Comparison Content */}
                        <div className="comparison-content">
                            {/* Stats Comparison Table */}
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
                                <div className="comparison-chart-container">
                                    <Bar data={comparisonChartData} options={{...chartOptions, maintainAspectRatio: false}} />
                                </div>
                            </div>
                        </div>
                    </div>
                ) : (
                    /* Single Run View (Original Layout) */
                    <>
                        {/* Run Selection Area */}
                        <div className="run-selection-section">
                            <h3 className="run-selection-title">Select Run</h3>
                            <div className="run-selection-grid">
                                {currentRuns.map((runId, index) => {
                                    const isSelected = selectedRunId === runId;
                                    return (
                                        <div 
                                            key={index}
                                            className={`run-card ${isSelected ? 'run-card-selected' : 'run-card-unselected'}`}
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

                        <div className="stats-grid">
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
                                title="Repository"
                                value={currentRepo?.split('/').pop() || 'N/A'}
                                label={currentJob}
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

                        <div className="env-info-card">
                            <h3 className="env-info-title">Environment Information</h3>
                            <EnvItem label="Environment" value={primaryRun.summary.environment?.name || 'N/A'} />
                            <EnvItem label="Python Version" value={primaryRun.summary.environment?.python_version || 'N/A'} />
                            <EnvItem label="Packages" value={primaryRun.summary.environment?.packages || 'N/A'} />
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}

export default SummaryTab;