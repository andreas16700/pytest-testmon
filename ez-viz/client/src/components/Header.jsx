import React, { useState, useEffect, useRef } from "react";
import {Github, LogOut} from "lucide-react";

function Header({user, handleLogout}) {
    const [dropdownOpen, setDropdownOpen] = useState(false);
    const dropdownRef = useRef(null);

    useEffect(() => {
        const handleClickOutside = (event) => {
            if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
                setDropdownOpen(false);
            }
        };
        document.addEventListener("mousedown", handleClickOutside);
        return () => document.removeEventListener("mousedown", handleClickOutside);
    }, []);

    return (
        <div className="bg-gradient-to-r from-indigo-600 to-purple-600 px-6 py-4">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-white">
                        Testmon Multi-Project Visualizer
                    </h1>
                    <p className="text-indigo-100 text-sm">
                        Intelligent test selection across repositories and jobs
                    </p>
                </div>

                {user && (
                    <div className="relative" ref={dropdownRef}>
                        <button
                            onClick={() => setDropdownOpen(!dropdownOpen)}
                            className="flex items-center gap-2 bg-white/10 hover:bg-white/20 rounded-lg py-2 px-3 transition-all duration-200"
                        >
                            {user.avatar_url && (
                                <img
                                    src={user.avatar_url}
                                    alt={user.login}
                                    className="w-8 h-8 rounded-full border-2 border-white/30"
                                />
                            )}
                            <span className="text-white text-sm font-medium">
                                {user.login}
                            </span>
                            <svg
                                className={`w-4 h-4 text-white/70 transition-transform duration-200 ${dropdownOpen ? 'rotate-180' : ''}`}
                                fill="none"
                                stroke="currentColor"
                                viewBox="0 0 24 24"
                            >
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                            </svg>
                        </button>

                        {dropdownOpen && (
                            <div className="absolute right-0 mt-2 w-56 bg-white rounded-lg shadow-xl border border-gray-100 py-1 z-50">
                                <div className="px-4 py-3 border-b border-gray-100">
                                    <div className="flex items-center gap-3">
                                        {user.avatar_url && (
                                            <img
                                                src={user.avatar_url}
                                                alt={user.login}
                                                className="w-10 h-10 rounded-full"
                                            />
                                        )}
                                        <div>
                                            <p className="text-sm font-semibold text-gray-800">
                                                {user.name || user.login}
                                            </p>
                                            <p className="text-xs text-gray-500">
                                                @{user.login}
                                            </p>
                                        </div>
                                    </div>
                                </div>

                                <a
                                    href={user.html_url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="flex items-center gap-2 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 transition-colors"
                                >
                                    <Github size={16} />
                                    View GitHub Profile
                                </a>

                                <div className="border-t border-gray-100 my-1"></div>

                                <button
                                    onClick={handleLogout}
                                    className="flex items-center gap-2 w-full px-4 py-2 text-sm text-red-600 hover:bg-red-50 transition-colors"
                                >
                                    <LogOut className="w-4 h-4" />
                                    Sign out
                                </button>
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}

export default Header;