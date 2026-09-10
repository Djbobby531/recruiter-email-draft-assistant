const BASE = "/api";

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: options.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data.detail || detail;
    } catch {
      // response body wasn't JSON - fall back to statusText
    }
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  health: () => request("/health"),

  gmailStatus: () => request("/gmail/status"),
  gmailOAuthStart: () => request("/gmail/oauth/start"),
  gmailDisconnect: () => request("/gmail/disconnect", { method: "POST" }),

  getProfile: () => request("/profile"),
  saveProfile: (payload) => request("/profile", { method: "PUT", body: JSON.stringify(payload) }),

  listResumes: () => request("/resumes"),
  uploadResume: (file) => {
    const form = new FormData();
    form.append("file", file);
    return request("/resumes", { method: "POST", body: form });
  },
  replaceResume: (id, file) => {
    const form = new FormData();
    form.append("file", file);
    return request(`/resumes/${id}/replace`, { method: "POST", body: form });
  },
  deleteResume: (id) => request(`/resumes/${id}`, { method: "DELETE" }),

  listMessages: (params = {}) => {
    const qs = new URLSearchParams(
      Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ""))
    ).toString();
    return request(`/messages${qs ? `?${qs}` : ""}`);
  },
  getMessage: (id) => request(`/messages/${id}`),
  createManualDraft: (payload) =>
    request("/messages/manual-draft", { method: "POST", body: JSON.stringify(payload) }),

  listApplications: (params = {}) => {
    const qs = new URLSearchParams(
      Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ""))
    ).toString();
    return request(`/applications${qs ? `?${qs}` : ""}`);
  },
  listManualReview: () => request("/applications/manual-review"),
  getApplication: (id) => request(`/applications/${id}`),
  getDraft: (id) => request(`/applications/${id}/draft`),
  resolveReview: (id, payload) =>
    request(`/applications/${id}/resolve-review`, { method: "POST", body: JSON.stringify(payload) }),
  skipApplication: (id) => request(`/applications/${id}/skip`, { method: "POST" }),
  updateApplicationStatus: (id, payload) =>
    request(`/applications/${id}/status`, { method: "POST", body: JSON.stringify(payload) }),
  syncSentApplications: () => request("/applications/sync-sent", { method: "POST" }),
  applicationsExportUrl: () => `${BASE}/applications/export.csv`,
  applicationResumeFileUrl: (id) => `${BASE}/applications/${id}/resume-file`,

  dashboardStats: () => request("/dashboard/stats"),
  dashboardCharts: () => request("/dashboard/charts"),

  getSettings: () => request("/settings"),
  getSettingsCustomization: () => request("/settings/customization"),
  updateSettingsCustomization: (payload) =>
    request("/settings/customization", { method: "PUT", body: JSON.stringify(payload) }),
  resetSettingsCustomization: () => request("/settings/customization/reset", { method: "POST" }),

  listRecruiters: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request(`/recruiters${qs ? `?${qs}` : ""}`);
  },
  topRecruiters: (limit = 10) => request(`/recruiters/top?limit=${limit}`),
  getRecruiter: (id) => request(`/recruiters/${id}`),
  getRecruiterOpportunities: (id) => request(`/recruiters/${id}/opportunities`),
  updateRecruiter: (id, payload) => request(`/recruiters/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteRecruiter: (id) => request(`/recruiters/${id}`, { method: "DELETE" }),
  deleteAllRecruiters: () => request("/recruiters", { method: "DELETE" }),

  listSkippedOpportunities: () => request("/opportunities/skipped"),
  getOpportunity: (id) => request(`/opportunities/${id}`),
  deleteOpportunity: (id) => request(`/opportunities/${id}`, { method: "DELETE" }),
  deleteAllOpportunityHistory: () => request("/opportunities", { method: "DELETE" }),
  overrideInterviewSkip: (id) => request(`/opportunities/${id}/override`, { method: "POST" }),
};
