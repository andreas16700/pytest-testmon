import React, {useEffect, useState} from "react";
import {Save, ChevronDown, GripVertical, Info, RotateCcw} from "lucide-react";
import {DragDropContext, Droppable, Draggable} from "@hello-pangea/dnd";
import {Toaster, toast} from "react-hot-toast";
import {formatDuration} from "./utils.jsx";

const API_BASE = "/api";

const reorder = (list, startIndex, endIndex) => {
    const result = Array.from(list);
    const [removed] = result.splice(startIndex, 1);
    result.splice(endIndex, 0, removed);
    return result;
};

// Multi-item reordering helper
const multiReorder = (list, selectedIds, insertAtIndex) => {
    const result = Array.from(list);
    const selectedItems = result.filter(item => selectedIds.includes(item.file_name));
    const unselectedItems = result.filter(item => !selectedIds.includes(item.file_name));

    unselectedItems.splice(insertAtIndex, 0, ...selectedItems);
    return unselectedItems;
};

function TestManagementTab({repos, currentRepo, currentJob}) {
    const [searchTerm, setSearchTerm] = useState("");
    const [isOpen, setIsOpen] = useState(false);
    const [testFileList, setTestFileList] = useState([]);
    const [expandedFile, setExpandedFile] = useState(null);
    const [alwaysRunTests, setAlwaysRunTests] = useState([]);
    const [prioritizedTests, setPrioritizedTests] = useState([]);
    const [loading, setLoading] = useState(false);
    const [failedAttemptId, setFailedAttemptId] = useState(null);

    const [selectedTests, setSelectedTests] = useState([]);
    const [lastSelectedTest, setLastSelectedTest] = useState(null);

    const filteredTests = testFileList.filter((testFile) =>
        testFile.file_name.toLowerCase().includes(searchTerm.toLowerCase())
    );

    const handleCheckboxChange = (testName) => {
        setAlwaysRunTests((prev) =>
            prev.includes(testName)
                ? prev.filter((filename) => filename !== testName)
                : [...prev, testName]
        );
    };

    const handleFileClick = (fileName) => {
        setExpandedFile(fileName === expandedFile ? null : fileName);
    };

    // Handle test selection for multi-drag
    const handleTestSelect = (testName, event) => {
        event.stopPropagation();

        if (event.ctrlKey || event.metaKey) {
            // Toggle selection with Ctrl/Cmd
            setSelectedTests(prev =>
                prev.includes(testName)
                    ? prev.filter(name => name !== testName)
                    : [...prev, testName]
            );
            setLastSelectedTest(testName);
        } else if (event.shiftKey && lastSelectedTest) {
            // Range selection with Shift
            const currentList = testFileList.map(t => t.file_name);
            const lastIndex = currentList.indexOf(lastSelectedTest);
            const currentIndex = currentList.indexOf(testName);

            const start = Math.min(lastIndex, currentIndex);
            const end = Math.max(lastIndex, currentIndex);
            const range = currentList.slice(start, end + 1);

            setSelectedTests(prev => {
                const newSelection = new Set([...prev, ...range]);
                return Array.from(newSelection);
            });
        } else {
            // Single selection
            setSelectedTests([testName]);
            setLastSelectedTest(testName);
        }
    };

    const handleSave = async () => {
        try {
            const orderedAlwaysRunTests = [
                ...prioritizedTests.filter(test => alwaysRunTests.includes(test)),
                ...alwaysRunTests.filter(test => !prioritizedTests.includes(test)),
            ];
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
                toast.success(`Test preferences have been successfully saved!`);
            } else {
                toast.error("Failed to save test preferences!");
            }
        } catch (error) {
            console.error("Error saving preferences:", error);
            toast.error("Test preferences couldn't be saved!");
        }
    };

    const handleReset = async () => {
        setAlwaysRunTests([]);
        setPrioritizedTests([]);
        setSearchTerm("");
        setExpandedFile(null);
        setSelectedTests([]);
        setLastSelectedTest(null);

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
                toast.success("Test preferences have been successfully reset!");
            } else {
                toast.error("Failed to reset test preferences!");
            }
        } catch (error) {
            console.error("Error resetting preferences:", error);
            toast.error("Test preferences couldn't be reset!");
        }
    };

    /* Send latest run id as parameter */
    useEffect(() => {
        if (!repos || repos.length === 0) return;

        const currentRepoLocal = repos.find((repo) => repo.id === currentRepo);
        if (!currentRepoLocal) {
            console.error("Repo not found:", currentRepo);
            return;
        }

        const currentJobLocal = currentRepoLocal.jobs.find((job) => job.name === currentJob);
        if (!currentJobLocal) {
            console.error("Job not found:", currentJob);
            return;
        }

        const latestRun = currentJobLocal.runs.at(-1);
        if (latestRun) {
            loadTestFileList(latestRun.id);
            loadTestPreferences();
        }
    }, [repos, currentRepo, currentJob]);

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

    const onDragStart = (result) => {
        setFailedAttemptId(null);

        const id = result.draggableId.replace(/^(available|prioritized)-/, '');

        // If dragging an unselected item, select only that item
        if (!selectedTests.includes(id)) {
            setSelectedTests([id]);
        }
    };

    const onDragEnd = (result) => {
        const {source, destination} = result;
        if (!destination) {
            return;
        }

        const draggedId = result.draggableId.replace(/^(available|prioritized)-/, '');

        // Get all items to move (either selected items or just the dragged item)
        const itemsToMove = selectedTests.length > 0 && selectedTests.includes(draggedId)
            ? selectedTests
            : [draggedId];

        let available = testFileList.filter((t) => !prioritizedTests.includes(t.file_name));
        let prioritized = testFileList.filter((t) => prioritizedTests.includes(t.file_name));

        // Moving within the same list
        if (source.droppableId === destination.droppableId) {
            if (source.droppableId === "prioritized") {
                if (itemsToMove.length > 1) {
                    // Multi-item reorder
                    prioritized = multiReorder(prioritized, itemsToMove, destination.index);
                } else {
                    // Single item reorder
                    prioritized = reorder(prioritized, source.index, destination.index);
                }
            } else {
                if (itemsToMove.length > 1) {
                    available = multiReorder(available, itemsToMove, destination.index);
                } else {
                    available = reorder(available, source.index, destination.index);
                }
            }
        }
        // Moving between lists
        else {
            if (source.droppableId === "available" && destination.droppableId === "prioritized") {
                // Remove items from available
                const itemsToMoveObjects = available.filter(t => itemsToMove.includes(t.file_name));
                available = available.filter(t => !itemsToMove.includes(t.file_name));

                // Add to prioritized at destination
                prioritized.splice(destination.index, 0, ...itemsToMoveObjects);
            } else {
                // Remove items from prioritized
                const itemsToMoveObjects = prioritized.filter(t => itemsToMove.includes(t.file_name));
                prioritized = prioritized.filter(t => !itemsToMove.includes(t.file_name));

                // Add to available at destination
                available.splice(destination.index, 0, ...itemsToMoveObjects);
            }
        }

        const newTestFileList = [...prioritized, ...available];
        const newPrioritizedTests = prioritized.map((t) => t.file_name);

        setTestFileList(newTestFileList);
        setPrioritizedTests(newPrioritizedTests);

        // Clear selection after drop
        setSelectedTests([]);
    };

    const renderDraggableItem = (test, index, listType) => {
        const isExpanded = test.file_name === expandedFile;
        const isSelected = selectedTests.includes(test.file_name);
        const methods = test.test_methods
            ? test.test_methods
                .split(",")
                .map((m) => m.trim())
                .filter((m) => m.length > 0)
            : [];

        return (
            <Draggable
                key={test.file_name}
                draggableId={`${listType}-${test.file_name}`}
                index={index}
            >
                {(provided, snapshot) => (
                    <React.Fragment>
                        <li
                            ref={provided.innerRef}
                            {...provided.draggableProps}
                            {...provided.dragHandleProps}
                            onClick={(e) => handleTestSelect(test.file_name, e)}
                            className={`draggable-item 
                                ${snapshot.isDragging ? "draggable-item-dragging" : "draggable-item-static"} 
                                ${isExpanded ? "draggable-item-expanded" : "draggable-item-collapsed"}
                                ${isSelected ? "draggable-item-selected" : ""}`}
                        >
                            <div className="draggable-content">
                                {isSelected && (
                                    <div className="selection-badge">
                                        {selectedTests.indexOf(test.file_name) + 1}
                                    </div>
                                )}
                                <GripVertical size={16} className="grip-icon"/>
                                <span className="file-name">{test.file_name}</span>
                                {methods.length > 0 && (
                                    <ChevronDown
                                        size={14}
                                        className={`chevron-icon text-gray-400 shrink-0 ${
                                            isExpanded ? "rotate-180" : ""
                                        }`}
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            handleFileClick(test.file_name);
                                        }}
                                    />
                                )}
                            </div>
                            <div className="info-icon-container">
                                <Info size={16} className="info-icon"/>
                                <div className="tooltip">
                                    <div className="tooltip-content">
                                        Took {formatDuration(test.total_duration)} last run
                                        <div className="tooltip-arrow"></div>
                                    </div>
                                </div>
                            </div>
                        </li>
                        {failedAttemptId === test.file_name && listType === "prioritized" && (
                            <div className="failed-attempt-message">
                                This test is in the priority list. It will run in priority order IF testmon selects it.
                                Check the box above to FORCE it to always run.
                            </div>
                        )}
                        {isExpanded && (
                            <ul className="draggable-methods-list">
                                {methods.length > 0 ? (
                                    methods.map((method, methodIndex) => (
                                        <li key={methodIndex} className="method-item">
                                            • {method}
                                        </li>
                                    ))
                                ) : (
                                    <li className="no-methods">No individual methods listed.</li>
                                )}
                            </ul>
                        )}
                    </React.Fragment>
                )}
            </Draggable>
        );
    };

    return loading ? (
        <div className="test-management-container">
            <Toaster />
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
                                const methods = test.test_methods ? test.test_methods.split(",").map((m) => m.trim()).filter((m) => m.length > 0) : [];
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
                                                    className={`chevron-icon text-gray-400 ${isExpanded ? "rotate-180" : ""}`}
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
                    Click to select tests (Ctrl/Cmd for multi-select, Shift for range). Drag selected tests to reorder or move between lists.
                </p>
                {selectedTests.length > 0 && (
                    <div className="px-3 py-2 bg-blue-500 text-white rounded mb-3 text-sm">
                        {selectedTests.length} test{selectedTests.length > 1 ? 's' : ''} selected
                        <button
                            onClick={() => setSelectedTests([])}
                            className="ml-3 px-2 py-0.5 bg-white bg-opacity-20 hover:bg-opacity-30 border-0 rounded text-white cursor-pointer"
                        >
                            Clear
                        </button>
                    </div>
                )}
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
                                            .map((test, index) => renderDraggableItem(test, index, "available"))}
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
                                            .map((test, index) => renderDraggableItem(test, index, "prioritized"))}
                                        {provided.placeholder}
                                    </ul>
                                </div>
                            )}
                        </Droppable>
                    </div>
                </DragDropContext>
            </div>

            <div className="save-button-container">
                <button onClick={handleSave} className="save-button">
                    <Save size={20}/>
                    Save Choices
                </button>
                <button onClick={handleReset} className="reset-button">
                    <RotateCcw size={20}/>
                    Reset Selections
                </button>
            </div>
            <div className="tip-box">
                <p className="tip-text">
                    <strong>Tip:</strong>
                    <br/>• <strong>Checked tests (Always Run):</strong> Will ALWAYS be forced to run in your priority order
                    <br/>• <strong>Prioritized but unchecked:</strong> Only run IF testmon selects them (due to changes), but in your priority order instead of duration order
                    <br/>• <strong>Multi-select:</strong> Click to select, Ctrl/Cmd+Click for multiple, Shift+Click for range
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