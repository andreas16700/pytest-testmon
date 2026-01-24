import React, {useEffect, useState} from "react";
import {Save, ChevronDown, GripVertical, Info, RotateCcw} from "lucide-react";
import {DragDropContext, Droppable, Draggable} from "@hello-pangea/dnd";

const API_BASE = "/api";

const reorder = (list, startIndex, endIndex) => {
    const result = Array.from(list);
    const [removed] = result.splice(startIndex, 1);
    result.splice(endIndex, 0, removed);
    return result;
};

function TestManagementTab({repos, currentRepo, currentJob, currentRuns}) {
    const [searchTerm, setSearchTerm] = useState("");
    const [isOpen, setIsOpen] = useState(false);
    const [testFileList, setTestFileList] = useState([]);
    const [expandedFile, setExpandedFile] = useState(null);
    const [alwaysRunTests, setAlwaysRunTests] = useState([]);
    const [prioritizedTests, setPrioritizedTests] = useState([]);
    const [loading, setLoading] = useState(false);
    const [failedAttemptId, setFailedAttemptId] = useState(null);

    const filteredTests = testFileList.filter((testFile) =>
        testFile.file_name.toLowerCase().includes(searchTerm.toLowerCase())
    );

    const handleCheckboxChange = (testName) => {
        setAlwaysRunTests((prev) =>
            prev.includes(testName)
                ? prev.filter((filename) => filename !== testName)
                : [...prev, testName]
        );
        // Don't automatically add to prioritized - user controls that separately
    };

    const handleFileClick = (fileName) => {
        setExpandedFile(fileName === expandedFile ? null : fileName);
    };

    const handleSave = async () => {
        try {
            // Order alwaysRunTests according to prioritizedTests order
            const orderedAlwaysRunTests = prioritizedTests.filter(test =>
                alwaysRunTests.includes(test)
            );

            const response = await fetch("/api/client/testPreferences", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({
                    repo_id: currentRepo,
                    job_id: currentJob,
                    alwaysRunTests: orderedAlwaysRunTests,
                    prioritizedTests: prioritizedTests,
                }),
            });
            if (response.ok) {
                alert(`Saved test choices`);
            } else {
                alert("Failed to save test preferences");
            }
        } catch (error) {
            console.error("Error saving preferences:", error);
            alert("Error saving test preferences");
        }
    };

    const handleReset = async () => {
        setAlwaysRunTests([]);
        setPrioritizedTests([]);
        setSearchTerm("");
        setExpandedFile(null);

        try {
            const response = await fetch("/api/client/testPreferences", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({
                    repo_id: currentRepo,
                    job_id: currentJob,
                    alwaysRunTests: [],
                    prioritizedTests: [],
                }),
            });
            if (response.ok) {
                alert("Selections reset and saved");
            } else {
                alert("Failed to reset preferences");
            }
        } catch (error) {
            console.error("Error resetting preferences:", error);
            alert("Error resetting preferences");
        }
    };

    /*Send latest run id as parameter */
    useEffect(() => {
        if (!repos || repos.length === 0) return;
        const currentRepoLocal = repos.find((repo) => repo.id === currentRepo);
        if (!currentRepoLocal) {
            console.error("Repo not found:", currentRepo);
        }
        const currentJobLocal = currentRepoLocal.jobs.find(
            (job) => job.name === currentJob
        );

        if (!currentJobLocal) {
            console.error("Job not found:", currentJob);
        }
        const latestRun = currentJobLocal.runs.at(-1);

        if (latestRun) {
            loadTestFileList(latestRun.id);
            loadTestPreferences();
        }
    }, [repos]);

    const loadTestPreferences = async () => {
        try {
            const response = await fetch(
                `${API_BASE}/client/testPreferences?repo_id=${currentRepo}&job_id=${currentJob}`
            );
            const data = await response.json();

            if (data.always_run_tests && data.always_run_tests.length > 0) {
                setAlwaysRunTests(data.always_run_tests);
            }
            if (data.prioritized_tests && data.prioritized_tests.length > 0) {
                setPrioritizedTests(data.prioritized_tests);
            }
        } catch (err) {
            console.error("Failed to load test preferences:", err);
        }
    };

    const loadTestFileList = async (run_id) => {
        try {
            const response = await fetch(
                `${API_BASE}/data/${currentRepo}/${currentJob}/${run_id}/test_files`
            );
            const data = await response.json();

            setTestFileList(data.test_files || []);
            setLoading(true);
        } catch (err) {
            console.error("Failed to load test file list:", err);
        }
    };

    const onDragStart = () => {
        setFailedAttemptId(null);
    };

    const onDragEnd = (result) => {
        const {source, destination} = result;
        if (!destination) return;

        setFailedAttemptId(null);

        let available = testFileList.filter(
            (t) => !prioritizedTests.includes(t.file_name)
        );
        let prioritized = testFileList.filter((t) =>
            prioritizedTests.includes(t.file_name)
        );

        // Case 1: Reordering within the same list
        if (source.droppableId === destination.droppableId) {
            if (source.droppableId === "prioritized") {
                prioritized = reorder(prioritized, source.index, destination.index);
            } else {
                available = reorder(available, source.index, destination.index);
            }
        }
        // Case 2: Moving between lists
        else {
            if (
                source.droppableId === "available" &&
                destination.droppableId === "prioritized"
            ) {
                const [moved] = available.splice(source.index, 1);
                prioritized.splice(destination.index, 0, moved);
            } else {
                // Moving from prioritized to available
                const [moved] = prioritized.splice(source.index, 1);
                available.splice(destination.index, 0, moved);
            }
        }

        const newTestFileList = [...prioritized, ...available];
        const newPrioritizedTests = prioritized.map((t) => t.file_name);

        setTestFileList(newTestFileList);
        setPrioritizedTests(newPrioritizedTests);
    };

    return loading ? (
        <div className="test-management-container">
            <h3 className="section-heading">Manage Tests</h3>
            <div className="select-tests-container">
                <button
                    onClick={() => setIsOpen(!isOpen)}
                    className="select-tests-button"
                >
                    Select Tests
                    <ChevronDown
                        size={18}
                        className={`chevron-icon ${isOpen ? "rotate-180" : ""}`}
                    />
                </button>
                {isOpen && (
                    <ul className="dropdown-list">
                        <li className="search-container">
                            <input
                                type="text"
                                placeholder="Search tests..."
                                className="search-input"
                                value={searchTerm}
                                onChange={(e) => setSearchTerm(e.target.value)}
                            />
                        </li>
                        {filteredTests.length > 0 ? (
                            filteredTests.map((test) => {
                                const id = `checkbox-${test.file_name}`;
                                const methods = test.test_methods
                                    ? test.test_methods
                                        .split(",")
                                        .map((m) => m.trim())
                                        .filter((m) => m.length > 0)
                                    : [];

                                const isExpanded = test.file_name === expandedFile;

                                return (
                                    <React.Fragment key={test.file_name}>
                                        <li
                                            className="list-item"
                                            onClick={() => handleFileClick(test.file_name)}
                                        >
                                            <label
                                                htmlFor={id}
                                                className="item-label"
                                                onClick={(e) => e.stopPropagation()}
                                            >
                                                <input
                                                    id={id}
                                                    type="checkbox"
                                                    checked={alwaysRunTests.includes(test.file_name)}
                                                    onChange={() => handleCheckboxChange(test.file_name)}
                                                    className="item-checkbox"
                                                />
                                                {test.file_name}
                                            </label>
                                            {methods.length > 0 && (
                                                <ChevronDown
                                                    size={14}
                                                    className={`chevron-icon text-gray-400 ${
                                                        isExpanded ? "rotate-180" : ""
                                                    }`}
                                                />
                                            )}
                                        </li>
                                        {isExpanded && (
                                            <ul className="methods-list">
                                                {methods.length > 0 ? (
                                                    <>
                                                        <li className="methods-header">
                                                            Test Functions ({methods.length})
                                                        </li>
                                                        {methods.map((method, methodIndex) => (
                                                            <li key={methodIndex} className="method-item">
                                                                • {method}
                                                            </li>
                                                        ))}
                                                    </>
                                                ) : (
                                                    <li className="no-methods">
                                                        No individual methods listed.
                                                    </li>
                                                )}
                                            </ul>
                                        )}
                                    </React.Fragment>
                                );
                            })
                        ) : (
                            <li className="no-tests-found">No tests found</li>
                        )}
                    </ul>
                )}
            </div>

            <div className="prioritization-section">
                <h3 className="section-heading">Test Prioritization</h3>
                <p className="section-description">
                    Drag tests from the left to prioritize them (right). You can reorder
                    the prioritized list.
                </p>
                <DragDropContext onDragEnd={onDragEnd} onDragStart={onDragStart}>
                    <div className="drag-drop-grid">
                        <Droppable droppableId="available">
                            {(provided) => (
                                <div
                                    ref={provided.innerRef}
                                    {...provided.droppableProps}
                                    className="droppable-container"
                                >
                                    <h4 className="droppable-title">Available Tests</h4>
                                    <ul className="draggable-list">
                                        {testFileList
                                            .filter((t) => !prioritizedTests.includes(t.file_name))
                                            .map((test, index) => {
                                                const isExpanded = test.file_name === expandedFile;
                                                const methods = test.test_methods
                                                    ? test.test_methods
                                                        .split(",")
                                                        .map((m) => m.trim())
                                                        .filter((m) => m.length > 0)
                                                    : [];

                                                return (
                                                    <Draggable
                                                        key={test.file_name}
                                                        draggableId={`available-${test.file_name}`}
                                                        index={index}
                                                    >
                                                        {(provided, snapshot) => (
                                                            <React.Fragment>
                                                                <li
                                                                    ref={provided.innerRef}
                                                                    {...provided.draggableProps}
                                                                    {...provided.dragHandleProps}
                                                                    onClick={() =>
                                                                        handleFileClick(test.file_name)
                                                                    }
                                                                    className={`draggable-item 
                                  ${
                                                                        snapshot.isDragging
                                                                            ? "draggable-item-dragging"
                                                                            : "draggable-item-static"
                                                                    } 
                                  ${
                                                                        isExpanded
                                                                            ? "draggable-item-expanded"
                                                                            : "draggable-item-collapsed"
                                                                    }`}
                                                                >
                                                                    <div className="draggable-content">
                                                                        <GripVertical
                                                                            size={16}
                                                                            className="grip-icon"
                                                                        />
                                                                        <span className="file-name">
                                      {test.file_name}
                                    </span>
                                                                        {methods.length > 0 && (
                                                                            <ChevronDown
                                                                                size={14}
                                                                                className={`chevron-icon text-gray-400 shrink-0 ${
                                                                                    isExpanded ? "rotate-180" : ""
                                                                                }`}
                                                                            />
                                                                        )}
                                                                    </div>
                                                                    <div className="info-icon-container">
                                                                        <Info size={16} className="info-icon"/>
                                                                        <div className="tooltip">
                                                                            <div className="tooltip-content">
                                                                                Took{" "}
                                                                                {(test.total_duration / 2).toFixed(4)}s
                                                                                last run
                                                                                <div className="tooltip-arrow"></div>
                                                                            </div>
                                                                        </div>
                                                                    </div>
                                                                </li>
                                                                {isExpanded && (
                                                                    <ul className="draggable-methods-list">
                                                                        {methods.length > 0 ? (
                                                                            methods.map((method, methodIndex) => (
                                                                                <li
                                                                                    key={methodIndex}
                                                                                    className="method-item"
                                                                                >
                                                                                    • {method}
                                                                                </li>
                                                                            ))
                                                                        ) : (
                                                                            <li className="no-methods">
                                                                                No individual methods listed.
                                                                            </li>
                                                                        )}
                                                                    </ul>
                                                                )}
                                                            </React.Fragment>
                                                        )}
                                                    </Draggable>
                                                );
                                            })}
                                        {provided.placeholder}
                                    </ul>
                                </div>
                            )}
                        </Droppable>

                        <Droppable droppableId="prioritized">
                            {(provided) => (
                                <div
                                    ref={provided.innerRef}
                                    {...provided.droppableProps}
                                    className="droppable-container"
                                >
                                    <h4 className="droppable-title">Prioritized Tests</h4>
                                    <ul className="draggable-list">
                                        {testFileList
                                            .filter((t) => prioritizedTests.includes(t.file_name))
                                            .map((test, index) => {
                                                const isExpanded = test.file_name === expandedFile;
                                                const methods = test.test_methods
                                                    ? test.test_methods
                                                        .split(",")
                                                        .map((m) => m.trim())
                                                        .filter((m) => m.length > 0)
                                                    : [];

                                                return (
                                                    <Draggable
                                                        key={test.file_name}
                                                        draggableId={`prioritized-${test.file_name}`}
                                                        index={index}
                                                    >
                                                        {(provided, snapshot) => (
                                                            <React.Fragment>
                                                                <li
                                                                    ref={provided.innerRef}
                                                                    {...provided.draggableProps}
                                                                    {...provided.dragHandleProps}
                                                                    onClick={() =>
                                                                        handleFileClick(test.file_name)
                                                                    }
                                                                    className={`draggable-item
                                                                                ${
                                                                        snapshot.isDragging
                                                                            ? "draggable-item-dragging"
                                                                            : "draggable-item-static"
                                                                    } 
                                                                                ${
                                                                        isExpanded
                                                                            ? "draggable-item-expanded"
                                                                            : "draggable-item-collapsed"
                                                                    }`}
                                                                >
                                                                    <div className="draggable-content">
                                                                        <GripVertical
                                                                            size={16}
                                                                            className="grip-icon"
                                                                        />
                                                                        <span className="file-name">
                                      {test.file_name}
                                    </span>
                                                                        {methods.length > 0 && (
                                                                            <ChevronDown
                                                                                size={14}
                                                                                className={`chevron-icon text-gray-400 shrink-0 ${
                                                                                    isExpanded ? "rotate-180" : ""
                                                                                }`}
                                                                            />
                                                                        )}
                                                                    </div>
                                                                    <div className="info-icon-container">
                                                                        <Info size={16} className="info-icon"/>
                                                                        <div className="tooltip">
                                                                            <div className="tooltip-content">
                                                                                Took{" "}
                                                                                {(test.total_duration / 2).toFixed(4)}s
                                                                                last run
                                                                                <div className="tooltip-arrow"></div>
                                                                            </div>
                                                                        </div>
                                                                    </div>
                                                                </li>
                                                                {failedAttemptId === test.file_name && (
                                                                    <div className="failed-attempt-message">
                                                                        This test is in the priority list. It will run
                                                                        in priority order IF testmon selects it.
                                                                        Check the box above to FORCE it to always run.
                                                                    </div>
                                                                )}
                                                                {isExpanded && (
                                                                    <ul className="draggable-methods-list">
                                                                        {methods.length > 0 ? (
                                                                            methods.map((method, methodIndex) => (
                                                                                <li
                                                                                    key={methodIndex}
                                                                                    className="method-item"
                                                                                >
                                                                                    • {method}
                                                                                </li>
                                                                            ))
                                                                        ) : (
                                                                            <li className="no-methods">
                                                                                No individual methods listed.
                                                                            </li>
                                                                        )}
                                                                    </ul>
                                                                )}
                                                            </React.Fragment>
                                                        )}
                                                    </Draggable>
                                                );
                                            })}
                                        {provided.placeholder}
                                    </ul>
                                </div>
                            )}
                        </Droppable>
                    </div>
                </DragDropContext>
            </div>

            <div
                className="save-button-container"
                style={{display: "flex", gap: "1rem", justifyContent: "center"}}
            >
                <button onClick={handleSave} className="save-button">
                    <Save size={20}/>
                    Save Choices
                </button>
                <button
                    onClick={handleReset}
                    className="save-button"
                    style={{backgroundColor: "#ef4444", borderColor: "#dc2626"}}
                >
                    <RotateCcw size={20}/>
                    Reset Selections
                </button>
            </div>
            <div className="tip-box">
                <p className="tip-text">
                    <strong>Tip:</strong>
                    <br/>• <strong>Checked tests (Always Run):</strong> Will ALWAYS be forced to run in your priority
                    order
                    <br/>• <strong>Prioritized but unchecked:</strong> Only run IF testmon selects them (due to
                    changes), but in your priority order instead of duration order
                    <br/>• Save your configuration for your CI pipeline.
                </p>
            </div>
        </div>
    ) : (
        <div className="loading-container">
            <div className="loading-spinner"></div>
            <span className="loading-text">Loading...</span>
        </div>
    );
}

export default TestManagementTab;
