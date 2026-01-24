import React, { useState } from "react";
import { IoIosCloseCircleOutline } from "react-icons/io";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { atomDark } from "react-syntax-highlighter/dist/cjs/styles/prism/index.js";
import { FaCopy, FaDownload } from "react-icons/fa";

function WorkflowFilePopup({ workflowFile, originalWorkflowFile, setIsPopupWindowOpen, user, repo, filePath }) {
    const [copyText, setCopyText] = useState("COPY CODE");
    const [isCommitting, setIsCommitting] = useState(false);

    const handleDownload = () => {
        const element = document.createElement("a");
        const file = new Blob([workflowFile], { type: 'text/yaml' });
        element.href = URL.createObjectURL(file);
        element.download = "ezmon_workflow.yml";
        document.body.appendChild(element); // Required for this to work in FireFox
        element.click();
        document.body.removeChild(element);
    };

    const handleCopy = () => {
        navigator.clipboard.writeText(workflowFile);
        setCopyText("COPIED");
    };

    const handleCommit = async () => {
        if (!workflowFile) return;

        setIsCommitting(true);

        try {
            const response = await fetch("/api/commit_workflow", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                credentials: "include",
                body: JSON.stringify({
                    owner: user.login,
                    repo: repo.name,
                    path: filePath,
                    content: workflowFile,
                    message: "ci: optimize workflow via Ezmon"
                }),
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || "Failed to commit");
            }

            // Success feedback
            alert("Successfully committed to GitHub!");
            setIsPopupWindowOpen(false);

        } catch (err) {
            console.error("Commit error:", err);
            alert(`Error committing file: ${err.message}`);
        } finally {
            setIsCommitting(false);
        }
    };

    const syntaxStyle = {
        margin: 0,
        padding: '1.5rem',
        background: 'transparent',
        fontSize: '0.85rem',
        lineHeight: '1.5',
    };

    return (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-black/90 p-4 md:p-10 pointer-events-auto animate-in fade-in zoom-in duration-300">
            <div className="bg-[#111] rounded-2xl border border-white/20 max-w-7xl w-full shadow-2xl flex flex-col h-[90vh]">

                <div className="flex justify-between items-center p-4 border-b border-white/10 bg-[#151515] rounded-t-xl flex-shrink-0">
                    <h3 className="text-white font-bold tracking-wide">Workflow Review</h3>
                    <button
                        onClick={() => { setIsPopupWindowOpen(false); setCopyText("COPY CODE") }}
                        className="text-gray-500 hover:text-white transition-colors"
                    >
                        <IoIosCloseCircleOutline size={"2em"} />
                    </button>
                </div>

                <div className="flex flex-col md:flex-row flex-1 overflow-hidden bg-[#1d1f21]">

                    <div className="w-full md:w-1/2 flex flex-col border-b md:border-b-0 md:border-r border-white/10 min-h-0">
                        <div className="bg-[#252525] px-4 py-2 text-xs font-bold text-gray-400 border-b border-white/5 uppercase tracking-wider sticky top-0 z-10">
                            Original File
                        </div>
                        <div className="overflow-auto custom-scrollbar flex-1">
                            <SyntaxHighlighter
                                language="yaml"
                                style={atomDark}
                                customStyle={syntaxStyle}
                                showLineNumbers={true}
                                wrapLongLines={false} /* Disabled wrapping to allow horizontal scroll */
                            >
                                {originalWorkflowFile || "# No original content found."}
                            </SyntaxHighlighter>
                        </div>
                    </div>

                    <div className="w-full md:w-1/2 flex flex-col min-h-0">
                        <div className="bg-[#004433] px-4 py-2 text-xs font-bold text-[#00ffcc] border-b border-white/5 uppercase tracking-wider sticky top-0 flex justify-between z-10">
                            <span>Generated Optimization</span>
                            <span className="text-[10px] bg-[#00ffcc] text-black px-2 rounded-full">AI UPDATED</span>
                        </div>
                        <div className="overflow-auto custom-scrollbar flex-1">
                            <SyntaxHighlighter
                                language="yaml"
                                style={atomDark}
                                customStyle={syntaxStyle}
                                showLineNumbers={true}
                                wrapLongLines={false} /* Disabled wrapping to allow horizontal scroll */
                            >
                                {workflowFile || "Generating content..."}
                            </SyntaxHighlighter>
                        </div>
                    </div>
                </div>

                <div className="p-4 justify-end bg-[#151515] rounded-b-xl flex gap-3 border-t border-white/10 flex-shrink-0">
                    <button
                        onClick={handleCopy}
                        className="px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white text-xs font-bold rounded-lg transition-colors flex items-center gap-2"
                    >
                        <FaCopy />{copyText}
                    </button>
                    <button
                        onClick={handleDownload}
                        className="px-6 py-2 bg-gray-700 hover:bg-gray-600 text-white text-xs font-bold rounded-lg transition-colors flex items-center gap-2"
                    >
                        <FaDownload /> DOWNLOAD GENERATED FILE
                    </button>
                    <button
                        onClick={handleCommit}
                        className="px-6 py-2 bg-gray-700 hover:bg-gray-600 text-white text-xs font-bold rounded-lg transition-colors flex items-center gap-2"
                    >
                        <FaDownload /> COMMIT
                    </button>
                </div>
            </div>
        </div>
    );
}

export default WorkflowFilePopup;