import React from "react";
import TestItem from "./TestItem.jsx";
import SearchBox from "./SearchBox.jsx";

function TestsTab({ allTests, search, setSearch, showTestDetails }) {
    console.log("all tests are" , allTests)
    const filteredTests = allTests.map(runData => ({
            ...runData,
            tests: runData.tests.filter(test =>
                test.test_name.toLowerCase().includes(search.toLowerCase())
            )
        })).filter(runData => runData.tests.length > 0);

    return (
        <div className="animate-fadeIn">
            <SearchBox
                value={search}
                onChange={setSearch}
                placeholder="🔍 Search tests..."
            />

            <div className="grid gap-4">
                {filteredTests.map(runData => runData.tests.map(test => (
                    <TestItem key={test.id} runId={runData.run_id} test={test} onClick={() => showTestDetails(test.id)} />
                )))}
            </div>
        </div>
    );
}

export default TestsTab;
