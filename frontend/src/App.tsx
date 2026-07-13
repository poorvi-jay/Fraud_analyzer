import { BrowserRouter, Route, Routes } from "react-router-dom";

import CaseDetail from "./pages/CaseDetail";
import CaseQueue from "./pages/CaseQueue";

export default function App() {
  return (
    <BrowserRouter>
      <div className="app">
        <header>
          <h1>Fraud investigation squad</h1>
        </header>
        <main>
          <Routes>
            <Route path="/" element={<CaseQueue />} />
            <Route path="/cases/:id" element={<CaseDetail />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}