import { NavLink } from "react-router-dom";

const links = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/gmail", label: "Gmail Connection" },
  { to: "/profile", label: "Candidate Profile" },
  { to: "/resumes", label: "Resume Library" },
  { to: "/messages", label: "Processed Emails" },
  { to: "/applications", label: "Applications / Drafts" },
  { to: "/recruiters", label: "Recruiters" },
  { to: "/skipped", label: "Skipped Opportunities" },
  { to: "/settings", label: "Settings" },
];

export default function Nav() {
  return (
    <nav className="nav">
      <div className="nav-title">Recruiter Email Draft Assistant</div>
      <ul>
        {links.map((l) => (
          <li key={l.to}>
            <NavLink to={l.to} end={l.end} className={({ isActive }) => (isActive ? "active" : "")}>
              {l.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
