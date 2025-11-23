import React from "react";
import EnvItem from "./EnvItem.jsx";
import StatCard from "./StatCard.jsx";
import {Doughnut} from "react-chartjs-2";
import {
    Chart as ChartJS,
    ArcElement,
    Tooltip,
    Legend,
} from "chart.js";

ChartJS.register(
    ArcElement,
    Tooltip,
    Legend
);

function SummaryTab({summary, allTests, currentRepo, currentJob}) {
    const passed = allTests.filter(t => !t.failed).length;
    const failed = allTests.filter(t => t.failed).length;
    const skipped = summary.savings.tests_saved ?? 0  ;
    const ran = summary.test_count-skipped;
    console.log("Summary is", summary)
    console.log("all tets are" , allTests)

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
            data: [],
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


    const runtimeSaved=summary.savings.time_saved ?? 0
    const runtimeSpent = summary.savings.time_all-runtimeSaved
   

    runtimeChartData.datasets[0].data = [runtimeSpent, runtimeSaved];

    return (
        <div className="animate-fadeIn">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-5 mb-8">
                <StatCard
                    title="Tests"
                    value={summary.test_count}
                    label={`${passed} passed, ${failed} failed, ${skipped} skipped`}
                />
                <StatCard
                    title="Files Tracked"
                    value={summary.file_count}
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
                <EnvItem label="Environment" value={summary.environment.name}/>
                <EnvItem label="Python Version" value={summary.environment.python_version}/>
                <EnvItem label="Packages" value={summary.environment.packages || 'N/A'}/>
            </div>
        </div>
    );
}

export default SummaryTab;