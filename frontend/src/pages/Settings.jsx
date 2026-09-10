import { useEffect, useState } from "react";
import { api } from "../api.js";

export default function Settings() {
  const [settings, setSettings] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.getSettings().then(setSettings).catch((e) => setError(e.message));
  }, []);

  return (
    <div>
      <h1>Settings</h1>
      <p className="muted">
        Configuration below is read from the backend&rsquo;s <code>.env</code> file and requires a restart to
        change. The customizations further down can be changed live, right here, with no restart needed.
      </p>
      {error && <div className="banner error">{error}</div>}
      {settings && (
        <table className="table kv">
          <tbody>
            <Row label="Environment" value={settings.app_env} />
            <Row label="Email detection mode" value={settings.email_mode} />
            <Row label="Poll interval (seconds)" value={settings.poll_interval_seconds} />
            <Row label="AI provider" value={settings.ai_provider} />
            <Row label="OpenAI API key configured" value={settings.openai_configured ? "yes" : "no"} />
            <Row label="Ollama base URL" value={settings.ollama_base_url} />
            <Row label="My email (excluded from recruiter detection)" value={settings.my_email} />
            <Row label="Job classification threshold" value={settings.job_classification_threshold} />
            <Row label="Recruiter email confidence threshold" value={settings.recruiter_email_confidence_threshold} />
          </tbody>
        </table>
      )}

      <h2 style={{ marginTop: 32 }}>Customization</h2>
      <p className="muted">
        Tweak these without editing any files or restarting the backend. Anything left blank (or that
        turns out invalid) automatically falls back to the built-in default shown as placeholder text -
        it never breaks email drafting or the Gmail poller.
      </p>
      <Customization />
    </div>
  );
}

function Row({ label, value }) {
  return (
    <tr>
      <td>{label}</td>
      <td>{value}</td>
    </tr>
  );
}

// ---- Customization -------------------------------------------------------

const EMPTY_FORM = {
  poll_interval_seconds: "",
  sent_sync_every_n_cycles: "",
  poll_backoff_max_seconds: "",
  allowed_sender_domains: "",
  disable_sender_filtering: false,
  show_hope_line: true,
  email_hope_line: "",
  email_capability_sentence: "",
  email_closing_line: "",
};

function toFormState(data) {
  return {
    poll_interval_seconds: data.poll_interval_seconds ?? "",
    sent_sync_every_n_cycles: data.sent_sync_every_n_cycles ?? "",
    poll_backoff_max_seconds: data.poll_backoff_max_seconds ?? "",
    allowed_sender_domains: data.allowed_sender_domains ?? "",
    disable_sender_filtering: data.allowed_sender_domains === "",
    show_hope_line: data.email_hope_line !== "",
    email_hope_line: data.email_hope_line ?? "",
    email_capability_sentence: data.email_capability_sentence ?? "",
    email_closing_line: data.email_closing_line ?? "",
  };
}

function toPayload(form) {
  const numOrNull = (v) => (v === "" ? null : Number(v));
  return {
    poll_interval_seconds: numOrNull(form.poll_interval_seconds),
    sent_sync_every_n_cycles: numOrNull(form.sent_sync_every_n_cycles),
    poll_backoff_max_seconds: numOrNull(form.poll_backoff_max_seconds),
    allowed_sender_domains: form.disable_sender_filtering
      ? ""
      : form.allowed_sender_domains === ""
      ? null
      : form.allowed_sender_domains,
    email_hope_line: !form.show_hope_line ? "" : form.email_hope_line === "" ? null : form.email_hope_line,
    email_capability_sentence: form.email_capability_sentence === "" ? null : form.email_capability_sentence,
    email_closing_line: form.email_closing_line === "" ? null : form.email_closing_line,
  };
}

function Customization() {
  const [data, setData] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);

  function load() {
    return api
      .getSettingsCustomization()
      .then((d) => {
        setData(d);
        setForm(toFormState(d));
      })
      .catch((e) => setError(e.message));
  }

  useEffect(() => {
    load();
  }, []);

  function update(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
    setSaved(false);
  }

  async function save(e) {
    e.preventDefault();
    setError(null);
    setSaving(true);
    try {
      const result = await api.updateSettingsCustomization(toPayload(form));
      setData(result);
      setForm(toFormState(result));
      setSaved(true);
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function resetAll() {
    setError(null);
    try {
      const result = await api.resetSettingsCustomization();
      setData(result);
      setForm(toFormState(result));
      setSaved(true);
    } catch (e) {
      setError(e.message);
    }
  }

  if (!data) return error ? <div className="banner error">{error}</div> : null;

  const invalid = new Set(data.invalid_fields || []);

  return (
    <form className="card form" style={{ maxWidth: 640 }} onSubmit={save}>
      {error && <div className="banner error">{error}</div>}
      {saved && <div className="banner success">Saved. Changes take effect on the next poll cycle - no restart needed.</div>}

      <h3>Polling &amp; Rate Limits</h3>
      <NumberField
        label="Poll interval (seconds)"
        value={form.poll_interval_seconds}
        onChange={(v) => update("poll_interval_seconds", v)}
        placeholder={String(data.default_poll_interval_seconds)}
        invalid={invalid.has("poll_interval_seconds")}
        hint="How often to check Gmail for new mail. Lower = faster drafts, but more API calls."
      />
      <NumberField
        label="Check sent-status every N poll cycles"
        value={form.sent_sync_every_n_cycles}
        onChange={(v) => update("sent_sync_every_n_cycles", v)}
        placeholder={String(data.default_sent_sync_every_n_cycles)}
        invalid={invalid.has("sent_sync_every_n_cycles")}
        hint="Checking whether a draft was actually sent costs one Gmail call per pending application - keep this above 1 to avoid rate limits."
      />
      <NumberField
        label="Max backoff after a rate limit (seconds)"
        value={form.poll_backoff_max_seconds}
        onChange={(v) => update("poll_backoff_max_seconds", v)}
        placeholder={String(data.default_poll_backoff_max_seconds)}
        invalid={invalid.has("poll_backoff_max_seconds")}
        hint="If Gmail rate-limits a poll, wait time doubles each retry up to this cap, then resets."
      />

      <h3>Sender Filtering</h3>
      <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
        <input
          type="checkbox"
          checked={form.disable_sender_filtering}
          onChange={(e) => update("disable_sender_filtering", e.target.checked)}
        />
        <span>Process email from every sender (disable domain filtering)</span>
      </label>
      {!form.disable_sender_filtering && (
        <TextField
          label="Only process senders from these domains (comma-separated)"
          value={form.allowed_sender_domains}
          onChange={(v) => update("allowed_sender_domains", v)}
          placeholder={data.default_allowed_sender_domains || "e.g. algebrait.com, pamten.com"}
          invalid={invalid.has("allowed_sender_domains")}
          hint="Mail from any other domain is skipped before it's ever fully downloaded."
        />
      )}

      <h3>Email Template</h3>
      <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
        <input
          type="checkbox"
          checked={form.show_hope_line}
          onChange={(e) => update("show_hope_line", e.target.checked)}
        />
        <span>Include a friendly opening line after &quot;Hi,&quot;</span>
      </label>
      {form.show_hope_line && (
        <TextField
          label="Opening line"
          value={form.email_hope_line}
          onChange={(v) => update("email_hope_line", v)}
          placeholder={data.default_email_hope_line}
          invalid={invalid.has("email_hope_line")}
        />
      )}
      <TextAreaField
        label="Capability sentence"
        value={form.email_capability_sentence}
        onChange={(v) => update("email_capability_sentence", v)}
        placeholder={data.default_email_capability_sentence}
        invalid={invalid.has("email_capability_sentence")}
        hint="Follows the skills sentence. Skills matched from the JD are still always appended automatically."
      />
      <TextAreaField
        label="Closing line"
        value={form.email_closing_line}
        onChange={(v) => update("email_closing_line", v)}
        placeholder={data.default_email_closing_line}
        invalid={invalid.has("email_closing_line")}
      />

      <div className="actions">
        <button type="submit" disabled={saving}>{saving ? "Saving…" : "Save Customization"}</button>
        <button type="button" className="secondary" onClick={resetAll}>Reset All to Defaults</button>
      </div>
    </form>
  );
}

function NumberField({ label, value, onChange, placeholder, invalid, hint }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input type="number" value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
      <FieldFooter invalid={invalid} placeholder={placeholder} hint={hint} />
    </label>
  );
}

function TextField({ label, value, onChange, placeholder, invalid, hint }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input type="text" value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
      <FieldFooter invalid={invalid} placeholder={placeholder} hint={hint} />
    </label>
  );
}

function TextAreaField({ label, value, onChange, placeholder, invalid, hint }) {
  return (
    <label className="field">
      <span>{label}</span>
      <textarea rows={2} value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
      <FieldFooter invalid={invalid} placeholder={placeholder} hint={hint} />
    </label>
  );
}

function FieldFooter({ invalid, placeholder, hint }) {
  return (
    <>
      {invalid && (
        <span className="badge warn" style={{ alignSelf: "flex-start" }}>
          Saved value is invalid - using default: &quot;{placeholder}&quot;
        </span>
      )}
      {hint && <span className="muted" style={{ fontSize: 12 }}>{hint}</span>}
    </>
  );
}
