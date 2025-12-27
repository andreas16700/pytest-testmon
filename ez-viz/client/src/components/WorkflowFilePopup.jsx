import React, {useState} from "react";
import {IoIosCloseCircleOutline} from "react-icons/io";
import {Prism as SyntaxHighlighter} from "react-syntax-highlighter";
import {atomDark} from "react-syntax-highlighter/dist/cjs/styles/prism/index.js";
import {FaCopy, FaDownload} from "react-icons/fa";

function WorkflowFilePopup({workflowFile, setIsPopupWindowOpen}) {
    const [copyText, setCopyText] = useState("COPY CODE");

    const handleDownload = () => {
        const element = document.createElement("a");
        const file = new Blob([workflowFile], {type: 'text/yaml'});
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

    return (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-black/90 p-10 pointer-events-auto animate-in fade-in zoom-in duration-300">
            <div className="bg-[#111] rounded-2xl border border-white/20 max-w-4xl w-full shadow-2xl flex flex-col max-h-[90vh]">
                <div className="flex justify-between items-center p-4 border-b border-white/10 bg-[#151515] rounded-t-xl flex-shrink-0">
                    <h3 className="text-white font-bold tracking-wide">Generated Workflow File</h3>
                    <button
                        onClick={() => {setIsPopupWindowOpen(false); setCopyText("COPY CODE")}}
                        className="text-gray-500 hover:text-white transition-colors"
                    >
                        <IoIosCloseCircleOutline size={"2em"}/>
                    </button>
                </div>

                <div className="overflow-auto custom-scrollbar p-0 bg-[#1d1f21] rounded-b-xl flex-1">
                    <SyntaxHighlighter
                        language="yaml"
                        style={atomDark}
                        customStyle={{
                            margin: 0,
                            padding: '1.5rem',
                            background: 'transparent',
                            fontSize: '0.9rem',
                            lineHeight: '1.5',
                        }}
                        showLineNumbers={true}
                        wrapLongLines={true}
                    >
                        {workflowFile || "No content generated yet..."}
                    </SyntaxHighlighter>
                </div>
                <div className="p-4 justify-end bg-[#151515] rounded-b-xl flex gap-3 border-t border-white/10">
                    <button
                        onClick={handleCopy}
                        className="px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white text-xs font-bold rounded-lg transition-colors flex items-center gap-2"
                    >
                        <FaCopy />{copyText}
                    </button>
                    <button
                        onClick={handleDownload}
                        className="px-6 py-2 bg-[#00ffcc] hover:bg-[#00eebb] text-black text-xs font-bold rounded-lg transition-colors flex items-center gap-2"
                    >
                        <FaDownload /> DOWNLOAD FILE
                    </button>
                </div>
            </div>
        </div>
    );
}

export default WorkflowFilePopup;