import { Routes, Route } from "react-router-dom";
import Nav from "./components/Nav.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import GmailConnection from "./pages/GmailConnection.jsx";
import CandidateProfile from "./pages/CandidateProfile.jsx";
import ResumeLibrary from "./pages/ResumeLibrary.jsx";
import ProcessedEmails from "./pages/ProcessedEmails.jsx";
import Applications from "./pages/Applications.jsx";
import Recruiters from "./pages/Recruiters.jsx";
import SkippedOpportunities from "./pages/SkippedOpportunities.jsx";
import Settings from "./pages/Settings.jsx";

export default function App() {
  return (
    <div className="layout">
      <Nav />
      <main className="content">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/gmail" element={<GmailConnection />} />
          <Route path="/profile" element={<CandidateProfile />} />
          <Route path="/resumes" element={<ResumeLibrary />} />
          <Route path="/messages" element={<ProcessedEmails />} />
          <Route path="/applications" element={<Applications />} />
          <Route path="/recruiters" element={<Recruiters />} />
          <Route path="/skipped" element={<SkippedOpportunities />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>
    </div>
  );
}
