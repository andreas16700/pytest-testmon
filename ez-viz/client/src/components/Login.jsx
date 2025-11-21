import React from "react";
import {ShieldCheck, Github} from "lucide-react";

function Login() {
    const handleLogin = () => {
        window.location.href = "/auth/github/login";
    };

    return (
        <div className="min-h-screen bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center p-5">
            <div className="bg-white rounded-xl shadow-2xl overflow-hidden w-full max-w-md">
                <div className="bg-gradient-to-r from-indigo-600 to-purple-600 px-8 py-6">
                    <h1 className="text-2xl font-bold text-white text-center">
                        Testmon Visualizer
                    </h1>
                    <p className="text-indigo-100 text-center mt-1 text-sm">
                        Get test execution insights
                    </p>
                </div>

                <div className="px-8 py-10">
                    <div className="text-center mb-8">
                        <div className="w-16 h-16 bg-gradient-to-br from-indigo-100 to-purple-100 rounded-full flex items-center justify-center mx-auto mb-4">
                            <ShieldCheck className="w-8 h-8 text-indigo-600" />
                        </div>
                        <h2 className="text-xl font-semibold text-gray-800">
                            Welcome Back
                        </h2>
                        <p className="text-gray-500 mt-2 text-sm">
                            Sign in with your GitHub account to view your repositories
                        </p>
                    </div>

                    <button
                        onClick={handleLogin}
                        className="w-full flex items-center justify-center gap-3 bg-gray-900 hover:bg-gray-800 text-white font-medium py-3 px-4 rounded-lg transition-all duration-200 shadow-lg hover:shadow-xl"
                    >
                        <Github />
                        Sign in with GitHub
                    </button>
                </div>

                <div className="px-8 py-4 bg-gray-50 border-t border-gray-100">
                    <p className="text-xs text-gray-400 text-center">
                        Only repositories you own or collaborate on will be visible.
                    </p>
                </div>
            </div>
        </div>
    );
}

export default Login;