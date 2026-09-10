import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";

export default function ResumeLibrary() {
  const [resumes, setResumes] = useState([]);
  const [error, setError] = useState(null);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef();
  const replaceInputs = useRef({});

  function load() {
    api.listResumes().then(setResumes).catch((e) => setError(e.message));
  }

  useEffect(load, []);

  async function handleUpload(e) {
    const file = e.target.files[0];
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      await api.uploadResume(file);
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  }

  async function handleReplace(id, e) {
    const file = e.target.files[0];
    if (!file) return;
    setError(null);
    try {
      await api.replaceResume(id, file);
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      e.target.value = "";
    }
  }

  async function handleDelete(id) {
    if (!confirm("Delete this resume?")) return;
    try {
      await api.deleteResume(id);
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <div>
      <h1>Resume Library</h1>
      {error && <div className="banner error">{error}</div>}
      <div className="card" style={{ marginBottom: 20 }}>
        <input ref={fileInput} type="file" accept=".pdf,.doc,.docx,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document" onChange={handleUpload} disabled={uploading} />
        {uploading && <span className="muted"> Uploading & indexing...</span>}
      </div>

      <table className="table">
        <thead>
          <tr>
            <th>Filename</th>
            <th>Status</th>
            <th>Extracted Skills</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {resumes.map((r) => (
            <tr key={r.id}>
              <td>{r.filename}</td>
              <td>
                <span className={`badge ${r.indexing_status === "INDEXED" ? "ok" : "warn"}`}>
                  {r.indexing_status}
                </span>
              </td>
              <td>
                <SkillsSummary metadata={r.extracted_metadata} />
              </td>
              <td className="actions">
                <label className="link-button">
                  Replace
                  <input
                    type="file"
                    accept=".pdf,.doc,.docx,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    hidden
                    onChange={(e) => handleReplace(r.id, e)}
                  />
                </label>
                <button className="danger" onClick={() => handleDelete(r.id)}>
                  Delete
                </button>
              </td>
            </tr>
          ))}
          {resumes.length === 0 && (
            <tr>
              <td colSpan={4} className="muted">
                No resumes uploaded yet. Upload 4-5 resumes (PDF, DOC, or DOCX) to get started.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function SkillsSummary({ metadata }) {
  const skills = metadata?.skills || {};
  const flat = Object.values(skills).flat();
  if (flat.length === 0) return <span className="muted">-</span>;
  return <span>{flat.slice(0, 10).join(", ")}{flat.length > 10 ? "..." : ""}</span>;
}
