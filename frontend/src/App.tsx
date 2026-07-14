import { BrowserRouter, Link, Route, Routes } from "react-router-dom";

import { AuthProvider, useAuth } from "./auth";
import Analytics from "./pages/Analytics";
import CaseDetail from "./pages/CaseDetail";
import CaseQueue from "./pages/CaseQueue";
import Login from "./pages/Login";

function HeaderAuth() {
  const { session, loading, signOut } = useAuth();
  if (loading) return null;
  return session ? (
    <span className="header-auth">
      Signed in as {session.user.email} &middot;{" "}
      <button className="link-button" onClick={() => signOut()}>
        sign out
      </button>
    </span>
  ) : (
    <Link to="/login" className="header-auth">
      Reviewer sign in
    </Link>
  );
}

function AppShell() {
  return (
    <BrowserRouter>
      <div className="app">
        <header>
          <div className="header-top">
            <h1>Fraud investigation squad</h1>
          </div>
          <div className="header-meta">
            <nav className="nav-links">
              <Link to="/">Case queue</Link>
              <Link to="/analytics">Analytics</Link>
            </nav>
            <HeaderAuth />
          </div>
        </header>
        <main>
          <Routes>
            <Route path="/" element={<CaseQueue />} />
            <Route path="/cases/:id" element={<CaseDetail />} />
            <Route path="/analytics" element={<Analytics />} />
            <Route path="/login" element={<Login />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AppShell />
    </AuthProvider>
  );
}
