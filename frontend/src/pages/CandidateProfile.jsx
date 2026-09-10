import { useEffect, useState } from "react";
import { api } from "../api.js";

const EMPTY = {
  name: "",
  experience: "",
  work_authorization: "",
  phone: "",
  email: "",
  linkedin: "",
};

export default function CandidateProfile() {
  const [form, setForm] = useState(EMPTY);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    api
      .getProfile()
      .then((p) => {
        if (!p) return;
        // only take the known editable fields - the GET response also
        // carries `id`/`updated_at`, which must never be echoed back in the
        // save payload (and must never silently grow to include a location
        // field either, however it might arrive).
        const next = {};
        for (const key of Object.keys(EMPTY)) next[key] = p[key] ?? "";
        setForm(next);
      })
      .catch((e) => setError(e.message));
  }, []);

  function update(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
    setSaved(false);
  }

  async function save(e) {
    e.preventDefault();
    setError(null);
    try {
      await api.saveProfile(form);
      setSaved(true);
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <div>
      <h1>Candidate Profile</h1>
      <p className="muted">
        These details are used in generated application emails. Your current/personal location is
        intentionally not collected here and will never appear in a generated email.
      </p>
      {error && <div className="banner error">{error}</div>}
      {saved && <div className="banner success">Profile saved.</div>}
      <form className="card form" onSubmit={save}>
        <Field label="Full name" value={form.name} onChange={(v) => update("name", v)} />
        <Field label="Years of experience" value={form.experience} onChange={(v) => update("experience", v)} placeholder="e.g. 8+ years" />
        <Field label="Work authorization" value={form.work_authorization} onChange={(v) => update("work_authorization", v)} placeholder="e.g. Authorized to work in the US" />
        <Field label="Phone" value={form.phone} onChange={(v) => update("phone", v)} />
        <Field label="Email" value={form.email} onChange={(v) => update("email", v)} />
        <Field label="LinkedIn" value={form.linkedin} onChange={(v) => update("linkedin", v)} />
        <button type="submit">Save Profile</button>
      </form>
    </div>
  );
}

function Field({ label, value, onChange, placeholder }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}
