import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api.js";

export default function Recruiters() {
  const [params, setParams] = useSearchParams();
  const [recruiters, setRecruiters] = useState([]);
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState("last_seen_at");
  const [uniquePhoneOnly, setUniquePhoneOnly] = useState(false);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [opportunities, setOpportunities] = useState([]);
  const [error, setError] = useState(null);
  const [notesDraft, setNotesDraft] = useState("");

  function load() {
    api.listRecruiters({ search, sort_by: sortBy, order: "desc", unique_phone: uniquePhoneOnly })
      .then(setRecruiters)
      .catch((e) => setError(e.message));
  }

  useEffect(load, [search, sortBy, uniquePhoneOnly]);

  useEffect(() => {
    const idParam = params.get("id");
    if (idParam) openDetail(Number(idParam));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function openDetail(id) {
    setSelected(id);
    setParams({ id: String(id) });
    try {
      const d = await api.getRecruiter(id);
      setDetail(d);
      setNotesDraft(d.notes || "");
      const opps = await api.getRecruiterOpportunities(id);
      setOpportunities(opps);
    } catch (e) {
      setError(e.message);
    }
  }

  async function saveNotes() {
    if (!selected) return;
    try {
      const updated = await api.updateRecruiter(selected, { notes: notesDraft });
      setDetail((d) => ({ ...d, notes: updated.notes }));
    } catch (e) {
      setError(e.message);
    }
  }

  async function handleDelete(id) {
    if (!confirm("Delete this recruiter and all their opportunity history?")) return;
    try {
      await api.deleteRecruiter(id);
      if (selected === id) {
        setSelected(null);
        setDetail(null);
      }
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  async function handleDeleteAll() {
    if (!confirm("Delete ALL stored recruiter data? This cannot be undone.")) return;
    try {
      await api.deleteAllRecruiters();
      setSelected(null);
      setDetail(null);
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <div className="split">
      <div>
        <h1>Recruiters</h1>
        {error && <div className="banner error">{error}</div>}
        <div className="card" style={{ marginBottom: 16, display: "flex", gap: 12, alignItems: "center" }}>
          <input
            placeholder="Search by name, email, company, or job title"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ flex: 1, padding: "8px 10px", border: "1px solid var(--border)", borderRadius: 6 }}
          />
          <select value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
            <option value="last_seen_at">Last seen</option>
            <option value="opportunities_count">Opportunities</option>
            <option value="drafts_count">Drafts</option>
            <option value="display_name">Name</option>
            <option value="company">Company</option>
          </select>
          <label style={{ display: "flex", alignItems: "center", gap: 6, whiteSpace: "nowrap" }}>
            <input
              type="checkbox"
              checked={uniquePhoneOnly}
              onChange={(e) => setUniquePhoneOnly(e.target.checked)}
            />
            Unique mobile numbers only
          </label>
          <button className="danger" onClick={handleDeleteAll}>
            Delete All
          </button>
        </div>

        <table className="table">
          <thead>
            <tr>
              <th>Recruiter</th>
              <th>Company</th>
              <th>Email</th>
              <th>Phone</th>
              <th>Opportunities</th>
              <th>Last Seen</th>
            </tr>
          </thead>
          <tbody>
            {recruiters.map((r) => (
              <tr
                key={r.id}
                className={selected === r.id ? "row-selected" : ""}
                style={{ cursor: "pointer" }}
                onClick={() => openDetail(r.id)}
              >
                <td>{r.display_name || <span className="muted">Unknown</span>}</td>
                <td>{r.company || <span className="muted">-</span>}</td>
                <td>{r.normalized_email}</td>
                <td>{r.phone || <span className="muted">-</span>}</td>
                <td>{r.opportunities_count}</td>
                <td>{new Date(r.last_seen_at).toLocaleDateString()}</td>
              </tr>
            ))}
            {recruiters.length === 0 && (
              <tr>
                <td colSpan={6} className="muted">
                  No recruiters recorded yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div>
        {detail && (
          <div className="card">
            <h2>{detail.display_name || detail.normalized_email}</h2>
            <DetailRow label="Email" value={detail.normalized_email} />
            <DetailRow label="Company" value={detail.company} />
            <DetailRow label="Phone" value={detail.phone} />
            <DetailRow label="Recruiter Role" value={detail.recruiter_role} />
            <DetailRow label="First Seen" value={new Date(detail.first_seen_at).toLocaleDateString()} />
            <DetailRow label="Last Seen" value={new Date(detail.last_seen_at).toLocaleDateString()} />

            <h3>Statistics</h3>
            <DetailRow label="Opportunities received" value={detail.opportunities_count} />
            <DetailRow label="Drafts created" value={detail.drafts_count} />
            <DetailRow label="Skipped (in-person interview)" value={detail.skipped_count} />

            <h3>Roles Sent</h3>
            {detail.roles && detail.roles.length > 0 ? (
              <ul>
                {detail.roles.map((role) => (
                  <li key={role.id}>
                    {role.job_title}{" "}
                    <span className="muted">
                      ({role.opportunity_count}x, last {new Date(role.last_seen_at).toLocaleDateString()})
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">No roles recorded.</p>
            )}

            <h3>Recent Opportunities</h3>
            {opportunities.length > 0 ? (
              opportunities.map((opp) => (
                <div key={opp.id} className="card" style={{ marginBottom: 8 }}>
                  <strong>{opp.job_title || "Unknown role"}</strong>
                  <div className="muted">{opp.job_location}</div>
                  <div className="muted">{new Date(opp.created_at).toLocaleDateString()}</div>
                  <span className={`badge ${opp.status === "DRAFT_CREATED" ? "ok" : opp.status.startsWith("SKIPPED") ? "warn" : ""}`}>
                    {opp.status}
                  </span>
                </div>
              ))
            ) : (
              <p className="muted">No opportunities recorded.</p>
            )}

            <h3>Notes</h3>
            <textarea
              value={notesDraft}
              onChange={(e) => setNotesDraft(e.target.value)}
              rows={3}
              style={{ width: "100%", padding: 8, border: "1px solid var(--border)", borderRadius: 6 }}
            />
            <div style={{ marginTop: 8, display: "flex", gap: 8 }}>
              <button onClick={saveNotes}>Save Notes</button>
              <button className="danger" onClick={() => handleDelete(detail.id)}>
                Delete Recruiter
              </button>
            </div>
          </div>
        )}
        {!detail && <p className="muted">Select a recruiter to view their history.</p>}
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
