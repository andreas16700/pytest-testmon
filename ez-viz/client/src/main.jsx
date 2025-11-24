import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App.jsx";
import Login from "./components/Login.jsx";
import {BrowserRouter, Routes, Route} from "react-router"

createRoot(document.getElementById('root')).render(
    <BrowserRouter>
        <Routes>
            <Route path={"/"} element={<Login />}/>
            <Route path={"/home"} element={<App />}/>
        </Routes>
    </BrowserRouter>
);
