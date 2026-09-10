import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";

export default function SkippedOpportunities() {
  const [opportunities, setOpportunities] = useState([]);
  const [selected, setSelected] = useState(null);
  const [recruiter, setRecruiter] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  function load() {
    api.listSkippedOpportunities().then(setOpportunities).catch((e) => setError(e.message));
  }

  useEffect(load, []);

  async function openDetail(opp) {
    setSelected(opp);
    setRecruiter(null);
    try {
      const r = await api.getRecruiter(opp.recruiter_id);
      setRecruiter(r);
    } catch {
      // recruiter lookup is best-effort for display purposes
    }
  }

  async function handleOverride(id) {
    if (
      !confirm(
        "Override the in-person-interview skip and continue processing? This will run resume matching, generate an application email, and create a Gmail draft."
      )
    )
      return;
    setBusy(true);
    setError(null);
    try {
      await api.overrideInterviewSkip(id);
      load();
      setSelected(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(id) {
    if (!confirm("Delete this skipped opportunity record?")) return;
    try {
      await api.deleteOpportunity(id);
      load();
      if (selected?.id === id) setSelected(null);
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <div className="split">
      <div>
        <h1>Skipped Opportunities</h1>
        <p className="muted">
          Job emails that were automatically skipped because the interview process requires in-person
          attendance. No draft was created for any of these.
        </p>
        {error && <div className="banner error">{error}</div>}

        <table className="table">
          <thead>
            <tr>
              <th>Job Title</th>
              <th>Interview</th>
              <th>Confidence</th>
              <th>Received</th>
            </tr>
          </thead>
          <tbody>
            {opportunities.map((opp) => (
              <tr
                key={opp.id}
                className={selected?.id === opp.id ? "row-selected" : ""}
                style={{ cursor: "pointer" }}
                onClick={() => openDetail(opp)}
              >
                <td>{opp.job_title || <span className="muted">Unknown</span>}</td>
                <td>
                  <span className="badge warn">{opp.interview_type}</span>
                </td>
                <td>{opp.interview_confidence != null ? `${Math.round(opp.interview_confidence * 100)}%` : "-"}</td>
                <td>{new Date(opp.created_at).toLocaleDateString()}</td>
              </tr>
            ))}
            {opportunities.length === 0 && (
              <tr>
                <td colSpan={4} className="muted">
                  No opportunities have been skipped for requiring an in-person interview.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div>
        {selected && (
          <div className="card">
            <h2>{selected.job_title || "Unknown role"}</h2>
            <div className="banner warn">
              <strong>SKIPPED — In-person interview required</strong>
            </div>

            <DetailRow label="Job Location" value={selected.job_location} />
            <DetailRow
              label="Recruiter"
              value={
                recruiter ? (
                  <Link to={`/recruiters?id=${recruiter.id}`}>{recruiter.display_name || recruiter.normalized_email}</Link>
                ) : null
              }
            />
            <DetailRow label="Interview Type" value={selected.interview_type} />
            <DetailRow
              label="Confidence"
              value={selected.interview_confidence != null ? `${Math.round(selected.interview_confidence * 100)}%` : null}
            />
            <DetailRow label="Reason" value={selected.interview_reason} />
            <h3>Evidence</h3>
            <pre className="email-body">{selected.interview_evidence || "(no excerpt captured)"}</pre>

            {selected.interview_overridden && (
              <div className="banner success">
                Overridden on {new Date(selected.interview_override_at).toLocaleString()} - a draft was created.
              </div>
            )}

            {!selected.interview_overridden && (
              <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
                <button disabled={busy} onClick={() => handleOverride(selected.id)}>
                  Override and Process
                </button>
                <button className="danger" onClick={() => handleDelete(selected.id)}>
                  Delete Record
                </button>
              </div>
            )}
          </div>
        )}
        {!selected && <p className="muted">Select a skipped opportunity to view details.</p>}
      </div>
    </div>
  );
}

function DetailRow({ label, value }) {
  return (
    <div className="detail-row">
      <span className="detail-label">{label}</span>
      <span>{value || <span className="muted">-</span>}</span>
    </div>
  );
}
