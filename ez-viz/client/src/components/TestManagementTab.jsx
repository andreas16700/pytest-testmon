import React, {useEffect, useState} from "react";
import {Save, ChevronDown, GripVertical, Info} from "lucide-react";
import {DragDropContext, Droppable, Draggable} from "@hello-pangea/dnd";


const API_BASE = '/api';

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
        setPrioritizedTests((prev) => {
            if (!prev.includes(testName)) {
                return [...prev, testName];
            } else {
                return prev;
            }
        });
    };

    const handleFileClick = (fileName) => {
        setExpandedFile(fileName === expandedFile ? null : fileName);
    };

    const handleSave = async () => {
        
        try {
            const response = await fetch('/api/client/testPreferences', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    repo_id: currentRepo,
                    job_id: currentJob,
                    alwaysRunTests: alwaysRunTests,
                    prioritizedLists: prioritizedTests
                })
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
    }

    useEffect(() => {
        if (!repos || repos.length === 0) return;

        let latestContext = null;

        for (const repo of repos) {
            for (const job of repo.jobs) {
                for (const run of job.runs) {
                    if (!latestContext || run.last_updated > latestContext.last_updated) {
                        latestContext = {
                            runId: run.id,
                            last_updated: run.last_updated
                        };
                    }
                }
            }
        }

        if (latestContext) {
            loadTestFileList(latestContext.runId);
        }
    }, [repos]);

    const loadTestFileList = async (run_id) => {
        try {
            const response = await fetch(`${API_BASE}/data/${currentRepo}/${currentJob}/${run_id}/test_files`);
            const data = await response.json();
            setTestFileList(data.test_files || []);
            setLoading(true);
        } catch (err) {
            console.error('Failed to load test file list:', err);
        }
    }

    const onDragStart = () => {
        setFailedAttemptId(null);
    };

    const onDragEnd = (result) => {
        const {source, destination} = result;
        if (!destination) return;

        setFailedAttemptId(null);

        let available = testFileList.filter((t) => !prioritizedTests.includes(t.file_name));
        let prioritized = testFileList.filter((t) => prioritizedTests.includes(t.file_name));

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
            if (source.droppableId === "available" && destination.droppableId === "prioritized") {
                const [moved] = available.splice(source.index, 1);
                prioritized.splice(destination.index, 0, moved);
            } else {
                const itemToMove = prioritized[source.index];

                if (alwaysRunTests.includes(itemToMove.file_name)) {
                    setFailedAttemptId(itemToMove.file_name);
                    return;
                }

                const [moved] = prioritized.splice(source.index, 1);
                available.splice(destination.index, 0, moved);
            }
        }

        const newTestFileList = [...prioritized, ...available];
        const newPrioritizedTests = prioritized.map((t) => t.file_name);

        setTestFileList(newTestFileList);
        setPrioritizedTests(newPrioritizedTests);
    };


    return (loading ?
        <div className="test-management-container">
            <h3 className="section-heading">Manage Tests</h3>
            <div className="select-tests-container">
                <button
                    onClick={() => setIsOpen(!isOpen)}
                    className="select-tests-button"
                >
                    Select Tests
                    <ChevronDown size={18} className={`chevron-icon ${isOpen ? "rotate-180" : ""}`}/>
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
                                    ? test.test_methods.split(',').map(m => m.trim()).filter(m => m.length > 0)
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
                                                <ChevronDown size={14}
                                                             className={`chevron-icon text-gray-400 ${isExpanded ? "rotate-180" : ""}`}/>
                                            )}
                                        </li>
                                        {isExpanded && (
                                            <ul className="methods-list">
                                                {methods.length > 0 ? (
                                                    <>
                                                        <li className="methods-header">Test
                                                            Functions ({methods.length})
                                                        </li>
                                                        {methods.map((method, methodIndex) => (
                                                            <li key={methodIndex}
                                                                className="method-item">
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
                            <li className="no-tests-found">
                                No tests found
                            </li>
                        )}
                    </ul>
                )}
            </div>

            <div className="prioritization-section">
                <h3 className="section-heading">Test Prioritization</h3>
                <p className="section-description">Drag tests from the left to prioritize them (right). You
                    can reorder the prioritized list.</p>
                <DragDropContext onDragEnd={onDragEnd} onDragStart={onDragStart}>
                    <div className="drag-drop-grid">
                        <Droppable droppableId="available">
                            {(provided) => (
                                <div ref={provided.innerRef} {...provided.droppableProps}
                                     className="droppable-container">
                                    <h4 className="droppable-title">Available Tests</h4>
                                    <ul className="draggable-list">
                                        {testFileList
                                            .filter((t) => !prioritizedTests.includes(t.file_name))
                                            .map((test, index) => {
                                                const isExpanded = test.file_name === expandedFile;
                                                const methods = test.test_methods
                                                    ? test.test_methods.split(',').map(m => m.trim()).filter(m => m.length > 0)
                                                    : [];

                                                return (
                                                    <Draggable key={test.file_name}
                                                               draggableId={`available-${test.file_name}`}
                                                               index={index}>
                                                        {(provided, snapshot) => (
                                                            <React.Fragment>
                                                                <li
                                                                    ref={provided.innerRef}
                                                                    {...provided.draggableProps}
                                                                    {...provided.dragHandleProps}
                                                                    onClick={() => handleFileClick(test.file_name)}
                                                                    className={`draggable-item 
                                                                                ${snapshot.isDragging ? "draggable-item-dragging" : "draggable-item-static"} 
                                                                                ${isExpanded ? 'draggable-item-expanded' : 'draggable-item-collapsed'}`}
                                                                >
                                                                    <div
                                                                        className="draggable-content">
                                                                        <GripVertical size={16}
                                                                                      className="grip-icon"/>
                                                                        <span
                                                                            className="file-name">{test.file_name}</span>
                                                                        {methods.length > 0 && (
                                                                            <ChevronDown size={14}
                                                                                         className={`chevron-icon text-gray-400 shrink-0 ${isExpanded ? "rotate-180" : ""}`}/>
                                                                        )}
                                                                    </div>
                                                                    <div
                                                                        className="info-icon-container">
                                                                        <Info size={16}
                                                                              className="info-icon"/>
                                                                        <div
                                                                            className="tooltip">
                                                                            <div
                                                                                className="tooltip-content">
                                                                                Took {(test.total_duration / 2).toFixed(4)}s
                                                                                last run
                                                                                <div
                                                                                    className="tooltip-arrow"></div>
                                                                            </div>
                                                                        </div>
                                                                    </div>
                                                                </li>
                                                                {isExpanded && (
                                                                    <ul className="draggable-methods-list">
                                                                        {methods.length > 0 ? methods.map((method, methodIndex) => (
                                                                            <li key={methodIndex}
                                                                                className="method-item">
                                                                                • {method}
                                                                            </li>
                                                                        )) : (
                                                                            <li className="no-methods">No
                                                                                individual methods listed.</li>
                                                                        )}
                                                                    </ul>
                                                                )}
                                                            </React.Fragment>
                                                        )}
                                                    </Draggable>
                                                )
                                            })}
                                        {provided.placeholder}
                                    </ul>
                                </div>
                            )}
                        </Droppable>

                        <Droppable droppableId="prioritized">
                            {(provided) => (
                                <div ref={provided.innerRef} {...provided.droppableProps}
                                     className="droppable-container">
                                    <h4 className="droppable-title">Prioritized Tests</h4>
                                    <ul className="draggable-list">
                                        {testFileList
                                            .filter((t) => prioritizedTests.includes(t.file_name))
                                            .map((test, index) => {
                                                const isExpanded = test.file_name === expandedFile;
                                                const methods = test.test_methods
                                                    ? test.test_methods.split(',').map(m => m.trim()).filter(m => m.length > 0)
                                                    : [];

                                                return (
                                                    <Draggable key={test.file_name}
                                                               draggableId={`prioritized-${test.file_name}`}
                                                               index={index}>
                                                        {(provided, snapshot) => (
                                                            <React.Fragment>
                                                                <li
                                                                    ref={provided.innerRef}
                                                                    {...provided.draggableProps}
                                                                    {...provided.dragHandleProps}
                                                                    onClick={() => handleFileClick(test.file_name)}
                                                                    className={`draggable-item
                                                                                ${snapshot.isDragging ? "draggable-item-dragging" : "draggable-item-static"} 
                                                                                ${isExpanded ? 'draggable-item-expanded' : 'draggable-item-collapsed'}`}
                                                                >
                                                                    <div
                                                                        className="draggable-content">
                                                                        <GripVertical size={16}
                                                                                      className="grip-icon"/>
                                                                        <span className="file-name">{test.file_name}</span>
                                                                        {methods.length > 0 && (
                                                                            <ChevronDown size={14} className={`chevron-icon text-gray-400 shrink-0 ${isExpanded ? "rotate-180" : ""}`}/>
                                                                        )}
                                                                    </div>
                                                                    <div
                                                                        className="info-icon-container">
                                                                        <Info size={16}
                                                                              className="info-icon"/>
                                                                        <div
                                                                            className="tooltip">
                                                                            <div
                                                                                className="tooltip-content">
                                                                                Took {(test.total_duration / 2).toFixed(4)}s
                                                                                last run
                                                                                <div
                                                                                    className="tooltip-arrow"></div>
                                                                            </div>
                                                                        </div>
                                                                    </div>
                                                                </li>
                                                                {failedAttemptId === test.file_name && (
                                                                    <div className="failed-attempt-message">
                                                                        Since this was selected to always run, it is prioritized by default. To remove it, first uncheck the box in the above component.
                                                                    </div>
                                                                )}
                                                                {isExpanded && (
                                                                    <ul className="draggable-methods-list">
                                                                        {methods.length > 0 ? methods.map((method, methodIndex) => (
                                                                            <li key={methodIndex}
                                                                                className="method-item">
                                                                                • {method}
                                                                            </li>
                                                                        )) : (
                                                                            <li className="no-methods">No individual methods listed.</li>
                                                                        )}
                                                                    </ul>
                                                                )}
                                                            </React.Fragment>
                                                        )}
                                                    </Draggable>
                                                )
                                            })}
                                        {provided.placeholder}
                                    </ul>
                                </div>
                            )}
                        </Droppable>
                    </div>
                </DragDropContext>
            </div>

            <div className="save-button-container">
                <button
                    onClick={handleSave}
                    className="save-button"
                >
                    <Save size={20}/>
                    Save Choices
                </button>
            </div>
            <div className="tip-box">
                <p className="tip-text">
                    <strong>Tip:</strong> You can search and select tests to always run AND/OR you can prioritize
                    test runs. Save your configuration for your CI pipeline.
                </p>
            </div>
        </div> :
        <div className="loading-container">
            <div className="loading-spinner"></div>
            <span className="loading-text">Loading...</span>
        </div>);
}

export default TestManagementTab;