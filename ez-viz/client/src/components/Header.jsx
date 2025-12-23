import React, { useState, useEffect, useRef } from "react";
import { Github, LogOut, ChevronDown, User } from "lucide-react";

function Header({ user, handleLogout }) {
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
        <header className="header-bg">
            <div className="flex items-center justify-between max-w-[1600px] mx-auto">
                <div>
                    <h1 className="brand-title">
                        Testmon Multi-Project Visualizer
                    </h1>
                    <p className="brand-subtitle">
                        Intelligent test selection & analysis
                    </p>
                </div>

                {user && (
                    <div className="relative" ref={dropdownRef}>
                        <button
                            onClick={() => setDropdownOpen(!dropdownOpen)}
                            className="user-pill"
                        >
                            {user.avatar_url ? (
                                <img
                                    src={user.avatar_url}
                                    alt={user.login}
                                    className="w-7 h-7 rounded-full border border-white/20 shadow-sm"
                                />
                            ) : (
                                <div className="w-7 h-7 rounded-full bg-indigo-400 flex items-center justify-center">
                                    <User size={14} className="text-white" />
                                </div>
                            )}
                            <span className="text-white text-sm font-semibold">
                                {user.login}
                            </span>
                            <ChevronDown 
                                size={16} 
                                className={`text-white/70 transition-transform duration-300 ${dropdownOpen ? 'rotate-180' : ''}`} 
                            />
                        </button>

                        {dropdownOpen && (
                            <div className="profile-dropdown">
                                <div className="px-4 py-4 border-b border-gray-50 bg-gray-50/50 rounded-t-2xl mb-1">
                                    <p className="text-sm font-bold text-gray-900 leading-none">
                                        {user.name || user.login}
                                    </p>
                                    <p className="text-xs text-gray-500 mt-1.5">
                                        @{user.login}
                                    </p>
                                </div>

                                <a
                                    href={user.html_url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="dropdown-link"
                                >
                                    <Github size={16} />
                                    <span>GitHub Profile</span>
                                </a>

                                <div className="h-px bg-gray-100 my-1 mx-2"></div>

                                <button
                                    onClick={handleLogout}
                                    className="dropdown-danger w-full text-left"
                                >
                                    <LogOut size={16} />
                                    <span>Sign out</span>
                                </button>
                            </div>
                        )}
                    </div>
                )}
            </div>
        </header>
    );
}

export default Header;