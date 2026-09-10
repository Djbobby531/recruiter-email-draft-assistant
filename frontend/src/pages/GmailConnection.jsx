import { useEffect, useState } from "react";
import { api } from "../api.js";

export default function GmailConnection() {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  function load() {
    api.gmailStatus().then(setStatus).catch((e) => setError(e.message));
  }

  useEffect(load, []);

  async function connect() {
    setBusy(true);
    setError(null);
    try {
      const { authorization_url } = await api.gmailOAuthStart();
      window.location.href = authorization_url;
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    try {
      await api.gmailDisconnect();
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <h1>Gmail Connection</h1>
      {error && <div className="banner error">{error}</div>}
      {status && status.connected ? (
        <div className="card">
          <p>
            <strong>Connected:</strong> {status.email_address}
          </p>
          <p className="muted">Last sync history ID: {status.last_history_id || "not yet synced"}</p>
          <button disabled={busy} onClick={disconnect}>
            Disconnect
          </button>
        </div>
      ) : (
        <div className="card">
          <p>No Gmail account connected yet.</p>
          <ol>
            <li>Click "Connect Gmail" below.</li>
            <li>Sign in and grant the requested (read + compose-draft only) permissions.</li>
            <li>You'll be redirected back here once connected.</li>
          </ol>
          <button disabled={busy} onClick={connect}>
            Connect Gmail
          </button>
          <p className="muted" style={{ marginTop: 12 }}>
            Requires GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET configured in the backend .env - see README.
          </p>
        </div>
      )}
    </div>
  );
}
