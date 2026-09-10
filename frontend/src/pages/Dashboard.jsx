import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";

export default function Dashboard() {
  const [stats, setStats] = useState(null);
  const [charts, setCharts] = useState(null);
  const [topRecruiters, setTopRecruiters] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.dashboardStats().then(setStats).catch((e) => setError(e.message));
    api.dashboardCharts().then(setCharts).catch(() => {});
    api.topRecruiters(5).then(setTopRecruiters).catch(() => {});
  }, []);

  return (
    <div>
      <h1>Dashboard</h1>
      {error && <div className="banner error">{error}</div>}
      {!stats && !error && <p>Loading...</p>}
      {stats && (
        <>
          <h2>Today</h2>
          <div className="stat-grid">
            <StatCard label="Emails processed" value={stats.emails_processed} />
            <StatCard label="Job opportunities" value={stats.job_opportunities} />
            <StatCard label="Drafts created" value={stats.drafts_created} />
            <StatCard label="Replies skipped" value={stats.replies_skipped} />
            <StatCard label="Duplicates skipped" value={stats.duplicates_skipped} />
            <StatCard label="In-person interview skipped" value={stats.in_person_interview_skipped} />
            <StatCard label="Manual review" value={stats.manual_review} />
            <StatCard label="Errors" value={stats.errors} accent={stats.errors > 0 ? "error" : undefined} />
          </div>

          <h2 style={{ marginTop: 32 }}>Application Tracker</h2>
          <p className="muted" style={{ marginTop: -8 }}>
            A Gmail draft existing is never counted as Sent or Submitted - only confirmed Gmail sent-message
            evidence, or your own manual status change, moves an application forward.
          </p>
          <div className="stat-grid">
            <StatCard label="Drafts (awaiting send)" value={stats.drafts_created} />
            <StatCard label="Sent" value={stats.sent_count} />
            <StatCard label="Submitted" value={stats.submitted_count} />
            <StatCard label="Interviews" value={stats.interview_count} accent="interview" />
            <StatCard label="Rejected" value={stats.rejected_count} />
            <StatCard label="Withdrawn" value={stats.withdrawn_count} />
            <StatCard label="On hold" value={stats.on_hold_count} />
            <StatCard
              label="Interview rate"
              value={stats.interview_rate != null ? `${stats.interview_rate}%` : "n/a"}
            />
          </div>

          <div className="stat-grid" style={{ marginTop: 14 }}>
            <StatCard label="Applications this week" value={stats.applications_this_week} />
            <StatCard label="Applications this month" value={stats.applications_this_month} />
            <StatCard label="Interviews this month" value={stats.interviews_this_month} />
            <StatCard label="Recruiters contacted" value={stats.recruiters_contacted} />
          </div>

          <div className="card" style={{ marginTop: 24 }}>
            <strong>Resume selected for latest job:</strong>{" "}
            {stats.latest_resume_selected || <span className="muted">none yet</span>}
          </div>
        </>
      )}

      {charts && (
        <div className="section-grid">
          <BarChartCard title="Applications by Status" points={charts.applications_by_status} />
          <BarChartCard title="Applications by Location" points={charts.applications_by_location} />
          <BarChartCard title="Applications by Resume" points={charts.applications_by_resume} />
        </div>
      )}

      <h2 style={{ marginTop: 32 }}>Top Recruiters</h2>
      <p className="muted" style={{ marginTop: -8 }}>
        Ranked by activity volume (opportunities sent), not by quality.
      </p>
      <table className="table">
        <thead>
          <tr>
            <th>Recruiter</th>
            <th>Company</th>
            <th>Opportunities</th>
            <th>Drafts</th>
            <th>Skipped</th>
          </tr>
        </thead>
        <tbody>
          {topRecruiters.map((r) => (
            <tr key={r.id}>
              <td>
                <Link to={`/recruiters?id=${r.id}`}>{r.display_name || r.normalized_email}</Link>
              </td>
              <td>{r.company || <span className="muted">-</span>}</td>
              <td>{r.opportunities_count}</td>
              <td>{r.drafts_count}</td>
              <td>{r.skipped_count}</td>
            </tr>
          ))}
          {topRecruiters.length === 0 && (
            <tr>
              <td colSpan={5} className="muted">
                No recruiter activity yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function StatCard({ label, value, accent }) {
  return (
    <div className={`card stat-card ${accent || ""}`}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

function BarChartCard({ title, points }) {
  const max = Math.max(1, ...(points || []).map((p) => p.value));
  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>{title}</h3>
      {(!points || points.length === 0) && <p className="muted">Not enough data yet.</p>}
      <div className="bar-chart">
        {(points || []).map((p) => (
          <div className="bar-chart-row" key={p.label}>
            <span>{p.label}</span>
            <div className="bar-chart-track">
              <div className="bar-chart-fill" style={{ width: `${(p.value / max) * 100}%` }} />
            </div>
            <span>{p.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
