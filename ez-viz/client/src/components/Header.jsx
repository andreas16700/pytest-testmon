import React, { useState, useEffect, useRef } from "react";
import { Github, LogOut, ChevronDown, User, Activity, Zap } from "lucide-react";

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
        <header className="header-bg border-b border-white/10 shadow-xl">
            <div className="flex items-center justify-between max-w-[1600px] mx-auto px-6 py-4">
                <div className="flex items-center gap-3">
                    <Zap className="w-7 h-7 text-yellow-300 fill-yellow-300" />
                    <div>
                        <h1 className="text-2xl font-bold text-white leading-tight tracking-tight">
                            Ezmon
                        </h1>
                        <p className="text-xs text-indigo-200 font-medium">
                            Smart Test Analysis
                        </p>
                    </div>
                </div>

                {user && (
                    <div className="relative" ref={dropdownRef}>
                        <button
                            onClick={() => setDropdownOpen(!dropdownOpen)}
                            className="user-pill hover:bg-white/15 transition-all duration-200 hover:shadow-lg hover:scale-105"
                        >
                            {user.avatar_url ? (
                                <img
                                    src={user.avatar_url}
                                    alt={user.login}
                                    className="w-8 h-8 rounded-full border-2 border-white/30 shadow-md ring-2 ring-indigo-400/30"
                                />
                            ) : (
                                <div className="w-8 h-8 rounded-full bg-gradient-to-br from-indigo-400 to-purple-500 flex items-center justify-center shadow-md ring-2 ring-indigo-400/30">
                                    <User size={16} className="text-white" />
                                </div>
                            )}
                            <span className="text-white text-sm font-semibold tracking-wide">
                                {user.login}
                            </span>
                            <ChevronDown 
                                size={16} 
                                className={`text-white/70 transition-transform duration-300 ${dropdownOpen ? 'rotate-180' : ''}`} 
                            />
                        </button>

                        {dropdownOpen && (
                            <div className="profile-dropdown shadow-2xl">
                                <div className="px-4 py-4 border-b border-gray-100 bg-gradient-to-br from-indigo-50 to-purple-50 rounded-t-2xl">
                                    {user.avatar_url && (
                                        <img
                                            src={user.avatar_url}
                                            alt={user.login}
                                            className="w-12 h-12 rounded-full border-2 border-indigo-200 shadow-md mb-3 ring-4 ring-indigo-100"
                                        />
                                    )}
                                    <p className="text-sm font-bold text-gray-900 leading-none">
                                        {user.name || user.login}
                                    </p>
                                    <p className="text-xs text-gray-500 mt-1.5 font-medium">
                                        @{user.login}
                                    </p>
                                </div>

                                <a
                                    href={user.html_url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="dropdown-link hover:bg-indigo-50 transition-colors"
                                >
                                    <Github size={16} />
                                    <span>GitHub Profile</span>
                                </a>

                                <div className="h-px bg-gray-100 my-1 mx-2"></div>

                                <button
                                    onClick={handleLogout}
                                    className="dropdown-danger w-full text-left hover:bg-red-50 transition-colors"
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