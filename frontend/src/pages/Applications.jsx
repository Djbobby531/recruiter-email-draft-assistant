import { useEffect, useState } from "react";
import { api } from "../api.js";

const STATUS_OPTIONS = [
  "DRAFT", "SENT", "SUBMITTED", "INTERVIEW", "REJECTED", "WITHDRAWN", "ON_HOLD", "SKIPPED", "MANUAL_REVIEW",
];

// Section 30's lifecycle graph, mirrored client-side purely to decide which
// options render without the "admin override" checkbox - the backend is the
// actual source of truth and re-validates every request regardless.
const ALLOWED_TRANSITIONS = {
  DRAFT: ["SENT", "WITHDRAWN"],
  SENT: ["SUBMITTED", "REJECTED", "WITHDRAWN"],
  SUBMITTED: ["INTERVIEW", "REJECTED", "WITHDRAWN", "ON_HOLD"],
  INTERVIEW: ["REJECTED", "WITHDRAWN", "ON_HOLD"],
  ON_HOLD: ["SUBMITTED", "INTERVIEW", "REJECTED", "WITHDRAWN"],
};

// Section 24: color + text label together, never color alone.
const STATUS_BADGE_CLASS = {
  DRAFT: "muted-badge",
  SENT: "info",
  SUBMITTED: "ok",
  INTERVIEW: "interview",
  REJECTED: "error",
  WITHDRAWN: "muted-badge",
  ON_HOLD: "warn",
  SKIPPED: "muted-badge",
  MANUAL_REVIEW: "warn",
};

const EVENT_LABELS = {
  EMAIL_RECEIVED: "Email received",
  JOB_DETECTED: "Job details detected",
  RESUME_SELECTED: "Resume selected",
  DRAFT_CREATED: "Gmail draft created",
  EMAIL_SENT: "Email sent from Gmail",
  APPLICATION_SUBMITTED: "Marked as submitted",
  INTERVIEW_REQUESTED: "Interview stage reached",
  INTERVIEW_SCHEDULED: "Interview scheduled",
  INTERVIEW_COMPLETED: "Interview completed",
  REJECTED: "Marked as rejected",
  WITHDRAWN: "Withdrawn",
  SKIPPED: "Skipped",
  MANUAL_OVERRIDE: "Manual status override",
};

function StatusBadge({ status }) {
  return <span className={`badge ${STATUS_BADGE_CLASS[status] || ""}`}>{status?.replace(/_/g, " ") || "-"}</span>;
}

export default function Applications() {
  const [applications, setApplications] = useState([]);
  const [selected, setSelected] = useState(null);
  const [draft, setDraft] = useState(null);
  const [error, setError] = useState(null);
  const [reviewEmail, setReviewEmail] = useState("");
  const [reviewName, setReviewName] = useState("");
  const [syncing, setSyncing] = useState(false);

  const [filters, setFilters] = useState({
    search: "", status: "", date_range: "", local_requirement: "",
    sort_by: "created_at", order: "desc",
  });
  const [nextStatus, setNextStatus] = useState("");
  const [override, setOverride] = useState(false);

  function load(currentFilters = filters) {
    api.listApplications(currentFilters).then(setApplications).catch((e) => setError(e.message));
  }

  useEffect(() => {
    load(filters);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters]);

  function updateFilter(key, value) {
    setFilters((f) => ({ ...f, [key]: value }));
  }

  async function openDetails(row) {
    setDraft(null);
    setNextStatus("");
    setOverride(false);
    setReviewEmail(row.recruiter_email || "");
    setReviewName(row.recruiter_name || "");
    try {
      const full = await api.getApplication(row.id);
      setSelected(full);
      setDraft(full.draft || null);
    } catch (e) {
      setSelected(row);
      setError(e.message);
    }
  }

  async function resolveReview() {
    if (!selected) return;
    setError(null);
    try {
      const d = await api.resolveReview(selected.id, {
        recruiter_email: reviewEmail,
        recruiter_name: reviewName || null,
      });
      setDraft(d);
      load();
      openDetails(selected);
    } catch (e) {
      setError(e.message);
    }
  }

  async function skip() {
    if (!selected) return;
    try {
      await api.skipApplication(selected.id);
      load();
      setSelected(null);
    } catch (e) {
      setError(e.message);
    }
  }

  async function applyStatusChange() {
    if (!selected || !nextStatus) return;
    setError(null);
    try {
      const updated = await api.updateApplicationStatus(selected.id, { status: nextStatus, override });
      load();
      openDetails(updated);
    } catch (e) {
      setError(e.message);
    }
  }

  async function syncSent() {
    setSyncing(true);
    setError(null);
    try {
      await api.syncSentApplications();
      load();
      if (selected) openDetails(selected);
    } catch (e) {
      setError(e.message);
    } finally {
      setSyncing(false);
    }
  }

  const allowedNext = selected ? ALLOWED_TRANSITIONS[selected.status] || [] : [];
  const showOverrideCheckbox = nextStatus && !allowedNext.includes(nextStatus);

  return (
    <div className="split">
      <div>
        <h1>Applications / Drafts</h1>
        {error && <div className="banner error">{error}</div>}

        <div className="filters-bar">
          <input
            className="search-input"
            type="search"
            placeholder="Search recruiter, job, location, partner, client, status..."
            value={filters.search}
            onChange={(e) => updateFilter("search", e.target.value)}
          />
          <select value={filters.status} onChange={(e) => updateFilter("status", e.target.value)}>
            <option value="">All statuses</option>
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>{s.replace(/_/g, " ")}</option>
            ))}
          </select>
          <select value={filters.local_requirement} onChange={(e) => updateFilter("local_requirement", e.target.value)}>
            <option value="">Local: any</option>
            <option value="YES">Local: required</option>
            <option value="PREFERRED">Local: preferred</option>
            <option value="NO">Local: not required</option>
            <option value="UNKNOWN">Local: unknown</option>
          </select>
          <select value={filters.date_range} onChange={(e) => updateFilter("date_range", e.target.value)}>
            <option value="">Any date</option>
            <option value="today">Today</option>
            <option value="yesterday">Yesterday</option>
            <option value="last_7_days">Last 7 days</option>
            <option value="last_30_days">Last 30 days</option>
            <option value="this_month">This month</option>
          </select>
          <select value={filters.sort_by} onChange={(e) => updateFilter("sort_by", e.target.value)}>
            <option value="created_at">Sort: Date</option>
            <option value="job_title">Sort: Job Title</option>
            <option value="job_location">Sort: Location</option>
            <option value="status">Sort: Status</option>
            <option value="recruiter_name">Sort: Recruiter</option>
          </select>
          <select value={filters.order} onChange={(e) => updateFilter("order", e.target.value)}>
            <option value="desc">Descending</option>
            <option value="asc">Ascending</option>
          </select>
          <a className="link-button" href={api.applicationsExportUrl()}>
            Export CSV
          </a>
          <button className="secondary" onClick={syncSent} disabled={syncing}>
            {syncing ? "Checking Gmail..." : "Check for Sent Emails"}
          </button>
        </div>

        <table className="table">
          <thead>
            <tr>
              <th>Status</th>
              <th>Recruiter</th>
              <th>Job</th>
              <th>Location</th>
              <th>Local</th>
              <th>Impl. Partner</th>
              <th>Resume</th>
              <th>Date</th>
            </tr>
          </thead>
          <tbody>
            {applications.map((a) => (
              <tr
                key={a.id}
                className={selected?.id === a.id ? "row-selected" : ""}
                onClick={() => openDetails(a)}
                style={{ cursor: "pointer" }}
              >
                <td><StatusBadge status={a.status} /></td>
                <td>{a.recruiter_name || a.recruiter_email || <span className="muted">Needs review</span>}</td>
                <td>{a.job_title || <span className="muted">Unknown</span>}</td>
                <td>{a.job_location || <span className="muted">-</span>}</td>
                <td>{a.local_requirement}</td>
                <td>{a.implementation_partner || <span className="muted">-</span>}</td>
                <td>{a.match_score != null ? `${a.match_score}%` : "-"}</td>
                <td>{a.created_at ? new Date(a.created_at).toLocaleDateString() : "-"}</td>
              </tr>
            ))}
            {applications.length === 0 && (
              <tr>
                <td colSpan={8} className="muted">
                  No applications match these filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div>
        {selected && (
          <div className="card">
            <div className="actions" style={{ justifyContent: "space-between" }}>
              <h2 style={{ marginBottom: 0 }}>{selected.job_title || "Unknown role"}</h2>
              <StatusBadge status={selected.status} />
            </div>

            {selected.status === "DRAFT" && (
              <div className="banner warn" style={{ marginTop: 12 }}>
                Gmail draft exists but has not been sent yet. <strong>Awaiting send confirmation</strong> - this
                will never be marked SENT until Gmail confirms the message was actually sent.
              </div>
            )}

            <h3>Job</h3>
            <DetailRow label="Job Title" value={selected.job_title} />
            <DetailRow label="Job Location" value={selected.job_location} />
            <DetailRow label="Local Needed" value={selected.local_requirement} />
            <DetailRow label="Employment Type" value={selected.employment_type} />

            <h3>Recruiter</h3>
            <DetailRow label="Recruiter Name" value={selected.recruiter_name} />
            <DetailRow label="Recruiter Email" value={selected.recruiter_email} />
            <DetailRow label="Recruiter Phone" value={selected.recruiter?.phone} />
            <DetailRow label="Original Sender (CC)" value={selected.cc_email} />

            <h3>Companies</h3>
            <DetailRow label="Implementation Partner" value={selected.implementation_partner} />
            <DetailRow label="End Client" value={selected.end_client} />

            <h3>Application</h3>
            <DetailRow label="Resume Match Score" value={selected.match_score != null ? `${selected.match_score}%` : "-"} />
            <DetailRow label="Match Explanation" value={selected.match_explanation} />
            <DetailRow label="Date Created" value={selected.created_at ? new Date(selected.created_at).toLocaleString() : "-"} />
            <DetailRow label="Date Sent" value={selected.sent_at ? new Date(selected.sent_at).toLocaleString() : "-"} />
            <DetailRow label="Date Submitted" value={selected.submitted_at ? new Date(selected.submitted_at).toLocaleString() : "-"} />
            <DetailRow label="Interview Status" value={selected.interview_status} />

            <h3>Gmail</h3>
            <DetailRow label="Thread ID" value={selected.thread_id} />
            <DetailRow label="Draft ID" value={draft?.gmail_draft_id} />
            <DetailRow label="Sent Message ID" value={selected.sent_message_id} />

            {selected.review_reason && (
              <div className="banner warn" style={{ marginTop: 12 }}>
                <strong>Manual review required:</strong> {selected.review_reason}
                <div className="candidate-list">
                  Candidate emails found: {(selected.candidate_recruiter_emails || []).join(", ") || "none"}
                </div>
                <div className="form" style={{ marginTop: 12 }}>
                  <label className="field">
                    <span>Recruiter email (TO)</span>
                    <input value={reviewEmail} onChange={(e) => setReviewEmail(e.target.value)} />
                  </label>
                  <label className="field">
                    <span>Recruiter name (optional)</span>
                    <input value={reviewName} onChange={(e) => setReviewName(e.target.value)} />
                  </label>
                  <div style={{ display: "flex", gap: 8 }}>
                    <button onClick={resolveReview} disabled={!reviewEmail}>
                      Create Draft With This Recruiter
                    </button>
                    <button className="secondary" onClick={skip}>
                      Skip
                    </button>
                  </div>
                </div>
              </div>
            )}

            {(selected.generated_subject || draft) && (
              <>
                <h3>Email</h3>
                <DetailRow label="Generated Subject" value={draft?.subject || selected.generated_subject} />
                <pre className="email-body">{selected.generated_body}</pre>
                <DetailRow
                  label="Attached Resume"
                  value={
                    draft?.attached_resume_filename && (
                      <>
                        {draft.attached_resume_filename}
                        {selected.has_customized_resume && (
                          <span className="badge interview" style={{ marginLeft: 8 }}>Customized</span>
                        )}
                      </>
                    )
                  }
                />
                {selected.selected_resume_id && (
                  <div style={{ marginTop: 8 }}>
                    <a
                      className="link-button"
                      href={api.applicationResumeFileUrl(selected.id)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {selected.has_customized_resume ? "View Customized Resume" : "View Attached Resume"}
                    </a>
                  </div>
                )}
              </>
            )}

            {draft?.gmail_draft_id && (
              <div style={{ marginTop: 12 }}>
                <a
                  className="link-button"
                  href="https://mail.google.com/mail/u/0/#drafts"
                  target="_blank"
                  rel="noreferrer"
                >
                  Open in Gmail
                </a>
              </div>
            )}

            <h3>Change Status</h3>
            <div className="actions">
              <select value={nextStatus} onChange={(e) => setNextStatus(e.target.value)}>
                <option value="">Select a status...</option>
                {STATUS_OPTIONS.filter((s) => s !== selected.status).map((s) => (
                  <option key={s} value={s}>
                    {s.replace(/_/g, " ")}
                    {allowedNext.includes(s) ? "" : " (override required)"}
                  </option>
                ))}
              </select>
              <button onClick={applyStatusChange} disabled={!nextStatus || (showOverrideCheckbox && !override)}>
                Apply
              </button>
            </div>
            {showOverrideCheckbox && (
              <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 6, marginTop: 8 }}>
                <input type="checkbox" checked={override} onChange={(e) => setOverride(e.target.checked)} />
                <span>This is not a normal transition - override lifecycle rules (logged for audit)</span>
              </label>
            )}

            <h3>Timeline</h3>
            <div className="timeline">
              {(selected.events || []).map((ev) => (
                <div className="timeline-item" key={ev.id}>
                  <div className="timeline-dot" />
                  <div>
                    <div className="timeline-time">{new Date(ev.event_timestamp).toLocaleString()}</div>
                    <div className="timeline-title">{EVENT_LABELS[ev.event_type] || ev.event_type}</div>
                  </div>
                </div>
              ))}
              {(!selected.events || selected.events.length === 0) && (
                <p className="muted">No timeline events recorded yet.</p>
              )}
            </div>
          </div>
        )}
        {!selected && <p className="muted">Select an application to view details.</p>}
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
