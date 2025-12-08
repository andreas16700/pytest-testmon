import React, { useState, useEffect } from "react";
import { Doughnut } from "react-chartjs-2";
import {
    Chart as ChartJS,
    ArcElement,
    Tooltip,
    Legend,
} from "chart.js";

ChartJS.register(ArcElement, Tooltip, Legend);

const API_BASE = '/api';

function PriorityTestResults({ testFileList, alwaysRunTests, prioritizedTests, currentRepo, currentJob, currentRuns }) {
    const [pytestSummary, setPytestSummary] = useState(null);
    const [pytestTests, setPytestTests] = useState(null);
    const [loading, setLoading] = useState(true);
    const [expandedTest, setExpandedTest] = useState(null);

    useEffect(() => {
        const fetchPytestData = async () => {
            if (!currentRepo || !currentJob || !currentRuns || currentRuns.length === 0) {
                setLoading(false);
                return;
            }

            try {
                // Get the latest run ID
                const latestRunId = currentRuns[0];
                
                // Fetch summary
                const summaryResponse = await fetch(`${API_BASE}/data/${currentRepo}/${currentJob}/${latestRunId}/pytest-summary`);
                if (summaryResponse.ok) {
                    const summaryData = await summaryResponse.json();
                    setPytestSummary(summaryData);
                }

                // Fetch test details
                const testsResponse = await fetch(`${API_BASE}/data/${currentRepo}/${currentJob}/${latestRunId}/pytest-tests`);
                if (testsResponse.ok) {
                    const testsData = await testsResponse.json();
                    setPytestTests(testsData);
                }
            } catch (error) {
                console.error('Failed to fetch pytest data:', error);
            } finally {
                setLoading(false);
            }
        };

        fetchPytestData();
    }, [currentRepo, currentJob, currentRuns]);
    // Calculate summary statistics
    const getSummaryStats = () => {
        const totalTests = testFileList.length;
        const alwaysRunCount = alwaysRunTests.length;
        const prioritizedCount = prioritizedTests.length;
        const unprioritizedCount = totalTests - prioritizedCount;
        
        // Calculate execution statistics from test file data
        let totalTestCount = 0;
        let forcedCount = 0;
        let failedCount = 0;
        
        testFileList.forEach(test => {
            totalTestCount += test.test_count || 0;
            forcedCount += test.forced_count || 0;
            failedCount += test.failed_count || 0;
        });
        
        const skippedCount = totalTestCount - forcedCount;
        
        return {
            totalTests,
            alwaysRunCount,
            prioritizedCount,
            unprioritizedCount,
            totalTestCount,
            forcedCount,
            skippedCount,
            failedCount
        };
    };

    const stats = getSummaryStats();

    // Chart data for Pytest Results
    const pytestResultsChartData = pytestSummary ? {
        labels: ["Passed", "Failed"],
        datasets: [
            {
                data: [pytestSummary.summary.passed, pytestSummary.summary.failed],
                backgroundColor: ["#10B981", "#EF4444"],
                borderColor: ["#059669", "#DC2626"],
                borderWidth: 2,
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
        <div style={{
            backgroundColor: '#f8f9fa',
            border: '1px solid #e0e0e0',
            borderRadius: '8px',
            padding: '20px',
            marginBottom: '24px'
        }}>
            <h4 style={{
                margin: '0 0 16px 0',
                fontSize: '16px',
                fontWeight: '600',
                color: '#333'
            }}>Test Preferences Summary</h4>
            
            {/* Pytest Summary Statistics */}
            {pytestSummary && pytestSummary.summary && (
                <div style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(250px, 1fr))',
                    gap: '20px',
                    marginBottom: '32px'
                }}>
                    <div style={{
                        backgroundColor: 'white',
                        padding: '24px',
                        borderRadius: '8px',
                        border: '1px solid #e0e0e0'
                    }}>
                        <div style={{
                            fontSize: '16px',
                            color: '#666',
                            marginBottom: '12px'
                        }}>Total Tests Collected</div>
                        <div style={{
                            fontSize: '48px',
                            fontWeight: 'bold',
                            color: '#64748b'
                        }}>{pytestSummary.summary.collected}</div>
                    </div>
                    
                    <div style={{
                        backgroundColor: 'white',
                        padding: '24px',
                        borderRadius: '8px',
                        border: '1px solid #e0e0e0'
                    }}>
                        <div style={{
                            fontSize: '16px',
                            color: '#666',
                            marginBottom: '12px'
                        }}>Tests Passed</div>
                        <div style={{
                            fontSize: '48px',
                            fontWeight: 'bold',
                            color: '#10B981'
                        }}>{pytestSummary.summary.passed}</div>
                        <div style={{
                            fontSize: '14px',
                            color: '#999',
                            marginTop: '8px'
                        }}>{pytestSummary.summary.total > 0 ? `${((pytestSummary.summary.passed / pytestSummary.summary.total) * 100).toFixed(1)}% of total` : '0%'}</div>
                    </div>
                    
                    <div style={{
                        backgroundColor: 'white',
                        padding: '24px',
                        borderRadius: '8px',
                        border: '1px solid #e0e0e0'
                    }}>
                        <div style={{
                            fontSize: '16px',
                            color: '#666',
                            marginBottom: '12px'
                        }}>Tests Failed</div>
                        <div style={{
                            fontSize: '48px',
                            fontWeight: 'bold',
                            color: '#EF4444'
                        }}>{pytestSummary.summary.failed}</div>
                        <div style={{
                            fontSize: '14px',
                            color: '#999',
                            marginTop: '8px'
                        }}>{pytestSummary.summary.total > 0 ? `${((pytestSummary.summary.failed / pytestSummary.summary.total) * 100).toFixed(1)}% of total` : '0%'}</div>
                    </div>
                    
                    <div style={{
                        backgroundColor: 'white',
                        padding: '24px',
                        borderRadius: '8px',
                        border: '1px solid #e0e0e0'
                    }}>
                        <div style={{
                            fontSize: '16px',
                            color: '#666',
                            marginBottom: '12px'
                        }}>Total Test Duration</div>
                        <div style={{
                            fontSize: '48px',
                            fontWeight: 'bold',
                            color: '#F59E0B'
                        }}>{pytestSummary.total_test_duration ? pytestSummary.total_test_duration.toFixed(4) : '0.0000'}s</div>
                        <div style={{
                            fontSize: '14px',
                            color: '#999',
                            marginTop: '8px'
                        }}>{pytestSummary.duration ? `Run duration: ${pytestSummary.duration.toFixed(4)}s` : ''}</div>
                    </div>
                </div>
            )}

            {/* Charts Grid */}
            {pytestResultsChartData && (
                <div style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))',
                    gap: '20px',
                    marginBottom: '24px'
                }}>
                    <div style={{
                        backgroundColor: 'white',
                        padding: '20px',
                        borderRadius: '8px',
                        border: '1px solid #e0e0e0'
                    }}>
                        <h3 style={{
                            fontSize: '16px',
                            fontWeight: '600',
                            color: '#333',
                            marginBottom: '16px',
                            textAlign: 'center'
                        }}>
                            Test Results Distribution
                        </h3>
                        <div style={{ maxWidth: "300px", margin: "0 auto" }}>
                            <Doughnut data={pytestResultsChartData} options={chartOptions} />
                        </div>
                    </div>
                </div>
            )}

            {/* Test Execution Details */}
            {pytestTests && pytestTests.tests && pytestTests.tests.length > 0 && (
                <div style={{
                    backgroundColor: 'white',
                    border: '1px solid #e0e0e0',
                    borderRadius: '8px',
                    padding: '24px'
                }}>
                    <h4 style={{
                        margin: '0 0 20px 0',
                        fontSize: '20px',
                        fontWeight: '600',
                        color: '#333'
                    }}>Test Execution Details ({pytestTests.tests.length} tests)</h4>
                    
                    <div style={{
                        maxHeight: '500px',
                        overflowY: 'auto',
                        border: '1px solid #e5e7eb',
                        borderRadius: '6px'
                    }}>
                        {pytestTests.tests.map((test, index) => {
                            const testFile = test.nodeid.split('::')[0];
                            const testFunction = test.nodeid.split('::')[1] || 'N/A';
                            const isExpanded = expandedTest === test.nodeid;
                            
                            return (
                                <div
                                    key={index}
                                    style={{
                                        borderBottom: index < pytestTests.tests.length - 1 ? '1px solid #e5e7eb' : 'none',
                                        padding: '16px 20px',
                                        cursor: 'pointer',
                                        backgroundColor: isExpanded ? '#f9fafb' : 'white',
                                        transition: 'background-color 0.2s'
                                    }}
                                    onClick={() => setExpandedTest(isExpanded ? null : test.nodeid)}
                                    onMouseEnter={(e) => e.currentTarget.style.backgroundColor = '#f9fafb'}
                                    onMouseLeave={(e) => e.currentTarget.style.backgroundColor = isExpanded ? '#f9fafb' : 'white'}
                                >
                                    <div style={{
                                        display: 'flex',
                                        alignItems: 'center',
                                        justifyContent: 'space-between',
                                        gap: '16px'
                                    }}>
                                        <div style={{ flex: 1, minWidth: 0 }}>
                                            <div style={{
                                                fontSize: '16px',
                                                fontWeight: '500',
                                                color: '#1f2937',
                                                marginBottom: '6px',
                                                overflow: 'hidden',
                                                textOverflow: 'ellipsis',
                                                whiteSpace: 'nowrap'
                                            }}>
                                                {testFunction}
                                            </div>
                                            <div style={{
                                                fontSize: '14px',
                                                color: '#6b7280',
                                                overflow: 'hidden',
                                                textOverflow: 'ellipsis',
                                                whiteSpace: 'nowrap'
                                            }}>
                                                {testFile}
                                            </div>
                                        </div>
                                        
                                        <div style={{
                                            display: 'flex',
                                            alignItems: 'center',
                                            gap: '16px',
                                            flexShrink: 0
                                        }}>
                                            <div style={{
                                                fontSize: '15px',
                                                fontWeight: '500',
                                                color: '#6b7280'
                                            }}>
                                                {test.duration ? `${test.duration.toFixed(4)}s` : 'N/A'}
                                            </div>
                                            
                                            <div style={{
                                                padding: '6px 12px',
                                                borderRadius: '6px',
                                                fontSize: '14px',
                                                fontWeight: '600',
                                                backgroundColor: test.outcome === 'passed' ? '#d1fae5' : 
                                                                test.outcome === 'failed' ? '#fee2e2' : '#e5e7eb',
                                                color: test.outcome === 'passed' ? '#065f46' : 
                                                       test.outcome === 'failed' ? '#991b1b' : '#374151'
                                            }}>
                                                {test.outcome || 'unknown'}
                                            </div>
                                        </div>
                                    </div>
                                    
                                    {isExpanded && test.error_message && (
                                        <div style={{
                                            marginTop: '14px',
                                            padding: '14px',
                                            backgroundColor: '#fef2f2',
                                            border: '1px solid #fecaca',
                                            borderRadius: '6px',
                                            fontSize: '13px',
                                            color: '#991b1b',
                                            fontFamily: 'monospace',
                                            whiteSpace: 'pre-wrap',
                                            wordBreak: 'break-word'
                                        }}>
                                            <strong>Error:</strong> {test.error_message}
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}
        </div>
    );
}

export default PriorityTestResults;
