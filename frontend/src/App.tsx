import { Route, BrowserRouter, Routes } from "react-router-dom";
import CaseDetail from "./pages/CaseDetail";
import CaseQueue from "./pages/CaseQueue";

export default function App() {
  return (
    <BrowserRouter>
      <div className="app">
        <header>
          <h1>Fraud investigation squad</h1>
          <div className="demo-banner">
            Demo mode &mdash; synthetic PaySim-derived data only. No real payment or user data is processed or
            stored.
          </div>
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
