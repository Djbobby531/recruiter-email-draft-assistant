import { useEffect, useState } from "react";
import { api } from "../api.js";

const STATUS_OPTIONS = [
  "RECEIVED", "PROCESSING", "DRAFT_CREATED", "MANUAL_REVIEW",
  "SKIPPED_REPLY", "SKIPPED_DUPLICATE", "SKIPPED_NOT_JOB",
  "SKIPPED_NO_RECRUITER_EMAIL", "SKIPPED_IN_PERSON_INTERVIEW",
  "SKIPPED_OWN_SENT_EMAIL", "SKIPPED_RECRUITER_ALREADY_CONTACTED_TODAY", "ERROR",
];

const STATUS_CLASS = {
  DRAFT_CREATED: "ok",
  RECEIVED: "info",
  PROCESSING: "info",
  MANUAL_REVIEW: "warn",
  SKIPPED_REPLY: "muted-badge",
  SKIPPED_DUPLICATE: "muted-badge",
  SKIPPED_NOT_JOB: "muted-badge",
  SKIPPED_NO_RECRUITER_EMAIL: "warn",
  SKIPPED_IN_PERSON_INTERVIEW: "muted-badge",
  SKIPPED_OWN_SENT_EMAIL: "muted-badge",
  SKIPPED_RECRUITER_ALREADY_CONTACTED_TODAY: "muted-badge",
  ERROR: "error",
};

export default function ProcessedEmails() {
  const [messages, setMessages] = useState([]);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [selected, setSelected] = useState(null);

  function load(params = { search, status }) {
    api.listMessages(params).then(setMessages).catch((e) => setError(e.message));
  }

  useEffect(() => {
    load({ search, status });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, status]);

  async function openDetails(row) {
    setError(null);
    try {
      const full = await api.getMessage(row.id);
      setSelected(full);
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <div className="split">
      <div>
        <h1>Processed Emails</h1>
        {error && <div className="banner error">{error}</div>}

        <ManualDraftBox onDrafted={() => load({ search, status })} />

        <div className="filters-bar">
          <input
            className="search-input"
            type="search"
            placeholder="Search subject, sender, or recipient..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>{s.replace(/_/g, " ")}</option>
            ))}
          </select>
        </div>

        <table className="table">
          <thead>
            <tr>
              <th>Subject</th>
              <th>From</th>
              <th>To</th>
              <th>Status</th>
              <th>Processed At</th>
            </tr>
          </thead>
          <tbody>
            {messages.map((m) => (
              <tr
                key={m.id}
                className={selected?.id === m.id ? "row-selected" : ""}
                onClick={() => openDetails(m)}
                style={{ cursor: "pointer" }}
              >
                <td>{m.subject || <span className="muted">(no subject)</span>}</td>
                <td>{m.from_email}</td>
                <td>{m.to_email || <span className="muted">-</span>}</td>
                <td>
                  <span className={`badge ${STATUS_CLASS[m.status] || ""}`}>{m.status}</span>
                </td>
                <td>{new Date(m.processed_at).toLocaleString()}</td>
              </tr>
            ))}
            {messages.length === 0 && (
              <tr>
                <td colSpan={5} className="muted">
                  No emails match these filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div>
        {selected && (
          <div className="card">
            <h2>{selected.subject || "(no subject)"}</h2>
            <div className="detail-row">
              <span className="detail-label">From</span>
              <span>{selected.from_email}</span>
            </div>
            <div className="detail-row">
              <span className="detail-label">To</span>
              <span>{selected.to_email || <span className="muted">-</span>}</span>
            </div>
            <div className="detail-row">
              <span className="detail-label">Status</span>
              <span className={`badge ${STATUS_CLASS[selected.status] || ""}`}>{selected.status}</span>
            </div>
            <div className="detail-row">
              <span className="detail-label">Processed At</span>
              <span>{new Date(selected.processed_at).toLocaleString()}</span>
            </div>
            {selected.error_message && (
              <div className="detail-row">
                <span className="detail-label">Error</span>
                <span className="muted">{selected.error_message}</span>
              </div>
            )}

            {selected.application ? (
              <>
                <h3>Resulting Application</h3>
                <div className="detail-row">
                  <span className="detail-label">Job Title</span>
                  <span>{selected.application.job_title || <span className="muted">-</span>}</span>
                </div>
                <div className="detail-row">
                  <span className="detail-label">Recruiter</span>
                  <span>{selected.application.recruiter_name || selected.application.recruiter_email || <span className="muted">-</span>}</span>
                </div>
                <div className="detail-row">
                  <span className="detail-label">Match Score</span>
                  <span>{selected.application.match_score != null ? `${selected.application.match_score}%` : "-"}</span>
                </div>
                {selected.application.selected_resume_id && (
                  <div style={{ marginTop: 12 }}>
                    <a
                      className="link-button"
                      href={api.applicationResumeFileUrl(selected.application.id)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {selected.application.has_customized_resume ? "View Customized Resume" : "View Attached Resume"}
                    </a>
                  </div>
                )}
              </>
            ) : (
              <p className="muted" style={{ marginTop: 16 }}>
                No application was generated for this message.
              </p>
            )}
          </div>
        )}
        {!selected && <p className="muted">Select a message to view details.</p>}
      </div>
    </div>
  );
}

const EMPTY_MANUAL_DRAFT = { sender_email: "", receiver_email: "", cc_email: "", subject: "", body: "" };

function ManualDraftBox({ onDrafted }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(EMPTY_MANUAL_DRAFT);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  function update(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  async function submit(e) {
    e.preventDefault();
    setError(null);
    setResult(null);
    setSubmitting(true);
    try {
      const res = await api.createManualDraft(form);
      setResult(res);
      setForm(EMPTY_MANUAL_DRAFT);
      onDrafted?.();
    } catch (e) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  }

  if (!open) {
    return (
      <button type="button" className="secondary" style={{ marginBottom: 16 }} onClick={() => setOpen(true)}>
        + Paste an Email to Draft
      </button>
    );
  }

  return (
    <form className="card form" style={{ maxWidth: 640, marginBottom: 20 }} onSubmit={submit}>
      <div className="actions" style={{ justifyContent: "space-between" }}>
        <h3 style={{ margin: 0 }}>Paste an Email to Draft</h3>
        <button type="button" className="secondary" onClick={() => setOpen(false)}>Close</button>
      </div>
      <p className="muted" style={{ margin: 0 }}>
        Runs the pasted text through the same pipeline as a real incoming email - resume
        matching/customization and a real Gmail draft - but you tell it exactly who to send to
        and CC instead of relying on auto-detection. Requires Gmail to be connected.
      </p>
      {error && <div className="banner error">{error}</div>}
      {result && (
        <div className={`banner ${result.status === "DRAFT_CREATED" ? "success" : "warn"}`}>
          <strong>{result.status.replace(/_/g, " ")}</strong>
          {result.recruiter_email && <> — to: {result.recruiter_email}</>}
          {result.cc_email && <> — cc: {result.cc_email}</>}
          {result.reason && <> — {result.reason}</>}
        </div>
      )}

      <label className="field">
        <span>Content to draft from</span>
        <textarea
          rows={10} required value={form.body}
          placeholder="Type or paste the job description / email text here..."
          onChange={(e) => update("body", e.target.value)}
        />
      </label>
      <label className="field">
        <span>Send to email</span>
        <input
          type="email" required value={form.receiver_email}
          placeholder="recruiter@company.com"
          onChange={(e) => update("receiver_email", e.target.value)}
        />
      </label>
      <label className="field">
        <span>CC email (optional)</span>
        <input
          type="email" value={form.cc_email}
          placeholder="someone-else@company.com"
          onChange={(e) => update("cc_email", e.target.value)}
        />
      </label>
      <label className="field">
        <span>Sender email (From, for record-keeping)</span>
        <input
          type="email" required value={form.sender_email}
          placeholder="recruiter@company.com"
          onChange={(e) => update("sender_email", e.target.value)}
        />
      </label>
      <label className="field">
        <span>Subject (optional)</span>
        <input
          type="text" value={form.subject}
          onChange={(e) => update("subject", e.target.value)}
        />
      </label>

      <button type="submit" disabled={submitting}>{submitting ? "Processing…" : "Create Draft"}</button>
    </form>
  );
}
