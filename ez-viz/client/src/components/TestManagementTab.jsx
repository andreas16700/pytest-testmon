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

function TestManagementTab({currentRepo, currentJob}) {
    const [searchTerm, setSearchTerm] = useState("");
    const [isOpen, setIsOpen] = useState(false);
    const [testFileList, setTestFileList] = useState([]);
    const [expandedFile, setExpandedFile] = useState(null);
    const [alwaysRunTests, setAlwaysRunTests] = useState([]);
    const [prioritizedTests, setPrioritizedTests] = useState([]);

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
        loadTestFileList();
    }, []);

    const loadTestFileList = async () => {
        try {
            const response = await fetch(`${API_BASE}/data/${currentRepo}/${currentJob}/test_files`);
            const data = await response.json();
            setTestFileList(data.test_files || []);
        } catch (err) {
            console.error('Failed to load test file list:', err);
        }
    }

    const onDragEnd = (result) => {
        const {source, destination} = result;
        if (!destination) return;

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
                const [moved] = prioritized.splice(source.index, 1);
                available.splice(destination.index, 0, moved);
            }
        }

        const newTestFileList = [...prioritized, ...available];
        const newPrioritizedTests = prioritized.map((t) => t.file_name);

        setTestFileList(newTestFileList);
        setPrioritizedTests(newPrioritizedTests);
    };


    return (
        <div className="animate-fadeIn p-6 max-w-2xl mx-auto">
            <h3 className="text-lg font-medium text-gray-800 mb-2">Manage Tests</h3>
            <div className="relative mb-6">
                <button
                    onClick={() => setIsOpen(!isOpen)}
                    className="flex items-center justify-between w-full px-5 py-2.5 rounded-md text-white text-sm font-medium bg-blue-600 hover:bg-blue-700 transition"
                >
                    Select Tests
                    <ChevronDown size={18} className={`ml-2 transition-transform ${isOpen ? "rotate-180" : ""}`} />
                </button>
                {isOpen && (
                    <ul className="absolute left-0 right-0 mt-2 bg-white border border-gray-200 shadow-lg rounded-md z-50 max-h-80 overflow-auto">
                        <li className="p-2 border-b border-gray-100 sticky top-0 bg-white z-10">
                            <input
                                type="text"
                                placeholder="Search tests..."
                                className="w-full px-3 py-2 text-sm border border-gray-200 rounded-md focus:outline-none focus:ring-1 focus:ring-blue-500"
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
                                            className="flex items-center justify-between px-4 py-2.5 hover:bg-gray-50 cursor-pointer"
                                            onClick={() => handleFileClick(test.file_name)}
                                        >
                                            <label
                                                htmlFor={id}
                                                className="flex items-center gap-3 text-gray-700 text-sm font-medium cursor-pointer"
                                                onClick={(e) => e.stopPropagation()}
                                            >
                                                <input
                                                    id={id}
                                                    type="checkbox"
                                                    checked={alwaysRunTests.includes(test.file_name)}
                                                    onChange={() => handleCheckboxChange(test.file_name)}
                                                    className="w-4 h-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500 cursor-pointer"
                                                />
                                                {test.file_name}
                                            </label>
                                            {methods.length > 0 && (
                                                <ChevronDown size={14} className={`ml-2 transition-transform text-gray-400 ${isExpanded ? "rotate-180" : ""}`} />
                                            )}
                                        </li>
                                        {isExpanded && (
                                            <ul className="pl-12 pr-4 pb-2 pt-1 bg-gray-50 border-t border-b border-gray-100">
                                                {methods.length > 0 ? (
                                                    <>
                                                        <li className="text-xs text-gray-500 font-semibold mb-1">Test Functions ({methods.length})</li>
                                                        {methods.map((method, methodIndex) => (
                                                            <li key={methodIndex} className="text-xs text-gray-600 truncate py-0.5">
                                                                • {method}
                                                            </li>
                                                        ))}
                                                    </>
                                                ) : (
                                                    <li className="text-xs text-gray-500 italic">
                                                        No individual methods listed.
                                                    </li>
                                                )}
                                            </ul>
                                        )}
                                    </React.Fragment>
                                );
                            })
                        ) : (
                            <li className="px-4 py-3 text-sm text-gray-500 text-center">
                                No tests found
                            </li>
                        )}
                    </ul>
                )}
            </div>

            <div className="mb-6">
                <h3 className="text-lg font-medium text-gray-800 mb-2">Test Prioritization</h3>
                <p className="text-gray-500 text-sm mb-4">Drag tests from the left to prioritize them (right). You can reorder the prioritized list.</p>
                <DragDropContext onDragEnd={onDragEnd}>
                    <div className="grid grid-cols-2 gap-6">
                        <Droppable droppableId="available">
                            {(provided) => (
                                <div ref={provided.innerRef} {...provided.droppableProps} className="bg-gray-50 border border-gray-200 rounded-lg shadow-sm p-3 min-h-[300px]" >
                                    <h4 className="text-gray-700 font-medium mb-2 text-sm">Available Tests</h4>
                                    <ul className="space-y-1">
                                        {testFileList
                                            .filter((t) => !prioritizedTests.includes(t.file_name))
                                            .map((test, index) => {
                                                const isExpanded = test.file_name === expandedFile;
                                                const methods = test.test_methods
                                                    ? test.test_methods.split(',').map(m => m.trim()).filter(m => m.length > 0)
                                                    : [];

                                                return (
                                                    <Draggable key={test.file_name} draggableId={`available-${test.file_name}`} index={index} >
                                                        {(provided, snapshot) => (
                                                            <React.Fragment>
                                                                <li
                                                                    ref={provided.innerRef}
                                                                    {...provided.draggableProps}
                                                                    {...provided.dragHandleProps}
                                                                    onClick={() => handleFileClick(test.file_name)}
                                                                    className={`flex items-start justify-between bg-white px-3 py-2 text-sm text-gray-700 border border-gray-100 cursor-pointer 
                                                                                ${snapshot.isDragging ? "shadow-lg" : "shadow-sm"} 
                                                                                ${isExpanded ? 'rounded-b-none' : 'rounded-md'}`}
                                                                >
                                                                    <div className="flex items-center flex-1 min-w-0">
                                                                        <GripVertical size={16} className="text-gray-400 mr-2 shrink-0"/>
                                                                        <span className="truncate">{test.file_name}</span>
                                                                        {methods.length > 0 && (
                                                                            <ChevronDown size={14} className={`ml-2 transition-transform text-gray-400 shrink-0 ${isExpanded ? "rotate-180" : ""}`} />
                                                                        )}
                                                                    </div>
                                                                    <div className="relative inline-block group ml-2 shrink-0">
                                                                        <Info size={16} className="text-gray-400 cursor-pointer" />
                                                                        <div className="absolute left-1/2 transform -translate-x-1/2 -top-8 opacity-0 group-hover:opacity-100 transition-opacity duration-200 pointer-events-none z-20">
                                                                            <div className="bg-gray-700 text-white text-xs rounded py-1 px-2 whitespace-nowrap relative">
                                                                                Took {(test.total_duration / 2).toFixed(4)}s last run
                                                                                <div className="absolute left-1/2 transform -translate-x-1/2 top-full w-0 h-0 border-l-4 border-r-4 border-t-4 border-l-transparent border-r-transparent border-t-gray-700"></div>
                                                                            </div>
                                                                        </div>
                                                                    </div>
                                                                </li>
                                                                {isExpanded && (
                                                                    <ul className="pl-8 pr-3 pb-2 pt-1 bg-white border-x border-b border-gray-100 rounded-b-md shadow-sm">
                                                                        {methods.length > 0 ? methods.map((method, methodIndex) => (
                                                                            <li key={methodIndex} className="text-xs text-gray-600 truncate py-0.5">
                                                                                • {method}
                                                                            </li>
                                                                        )) : (
                                                                            <li className="text-xs text-gray-500 italic">No individual methods listed.</li>
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
                                <div ref={provided.innerRef} {...provided.droppableProps} className="bg-gray-50 border border-gray-200 rounded-lg shadow-sm p-3 min-h-[300px]" >
                                    <h4 className="text-gray-700 font-medium mb-2 text-sm">Prioritized Tests</h4>
                                    <ul className="space-y-1">
                                        {testFileList
                                            .filter((t) => prioritizedTests.includes(t.file_name))
                                            .map((test, index) => {
                                                const isExpanded = test.file_name === expandedFile;
                                                const methods = test.test_methods
                                                    ? test.test_methods.split(',').map(m => m.trim()).filter(m => m.length > 0)
                                                    : [];

                                                return (
                                                    <Draggable key={test.file_name} draggableId={`prioritized-${test.file_name}`} index={index} >
                                                        {(provided, snapshot) => (
                                                            <React.Fragment>
                                                                <li
                                                                    ref={provided.innerRef}
                                                                    {...provided.draggableProps}
                                                                    {...provided.dragHandleProps}
                                                                    onClick={() => handleFileClick(test.file_name)}
                                                                    className={`flex items-start justify-between bg-white px-3 py-2 text-sm text-gray-700 border border-gray-100 cursor-pointer
                                                                                ${snapshot.isDragging ? "shadow-lg" : "shadow-sm"} 
                                                                                ${isExpanded ? 'rounded-b-none' : 'rounded-md'}`}
                                                                >
                                                                    <div className="flex items-center flex-1 min-w-0">
                                                                        <GripVertical size={16} className="text-gray-400 mr-2 shrink-0"/>
                                                                        <span className="truncate">{test.file_name}</span>
                                                                        {methods.length > 0 && (
                                                                            <ChevronDown size={14} className={`ml-2 transition-transform text-gray-400 shrink-0 ${isExpanded ? "rotate-180" : ""}`} />
                                                                        )}
                                                                    </div>
                                                                    <div className="relative inline-block group ml-2 shrink-0">
                                                                        <Info size={16} className="text-gray-400 cursor-pointer" />
                                                                        <div className="absolute left-1/2 transform -translate-x-1/2 -top-8 opacity-0 group-hover:opacity-100 transition-opacity duration-200 pointer-events-none z-20">
                                                                            <div className="bg-gray-700 text-white text-xs rounded py-1 px-2 whitespace-nowrap relative">
                                                                                Took {(test.total_duration / 2).toFixed(4)}s last run
                                                                                <div className="absolute left-1/2 transform -translate-x-1/2 top-full w-0 h-0 border-l-4 border-r-4 border-t-4 border-l-transparent border-r-transparent border-t-gray-700"></div>
                                                                            </div>
                                                                        </div>
                                                                    </div>
                                                                </li>
                                                                {isExpanded && (
                                                                    <ul className="pl-8 pr-3 pb-2 pt-1 bg-white border-x border-b border-gray-100 rounded-b-md shadow-sm">
                                                                        {methods.length > 0 ? methods.map((method, methodIndex) => (
                                                                            <li key={methodIndex} className="text-xs text-gray-600 truncate py-0.5">
                                                                                • {method}
                                                                            </li>
                                                                        )) : (
                                                                            <li className="text-xs text-gray-500 italic">No individual methods listed.</li>
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

            <div className="mb-6 flex gap-4 justify-center">
                <button
                    onClick={handleSave}
                    className="px-6 py-3 bg-indigo-500 text-white rounded-lg font-semibold hover:bg-indigo-600 transition-all flex items-center gap-2"
                >
                    <Save size={20}/>
                    Save Choices
                </button>
            </div>
            <div className="bg-yellow-50 border-l-4 border-yellow-400 p-4 rounded-md shadow-sm">
                <p className="text-yellow-800 text-sm leading-relaxed">
                    <strong>Tip:</strong> You can search and select tests to always run AND/OR you can prioritize test runs. Save your configuration for your CI pipeline.
                </p>
            </div>
        </div>
    );
}

export default TestManagementTab;