import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Dashboard from "./Dashboard.jsx";
import { api } from "../api.js";

vi.mock("../api.js", () => ({
  api: { dashboardStats: vi.fn(), dashboardCharts: vi.fn(), topRecruiters: vi.fn() },
}));

function renderDashboard() {
  return render(
    <MemoryRouter>
      <Dashboard />
    </MemoryRouter>
  );
}

describe("Dashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.topRecruiters.mockResolvedValue([]);
    api.dashboardCharts.mockResolvedValue({
      applications_by_status: [], applications_by_location: [], applications_by_resume: [],
    });
  });

  it("renders today's stats from the API", async () => {
    api.dashboardStats.mockResolvedValue({
      emails_processed: 100, job_opportunities: 42, replies_skipped: 11,
      duplicates_skipped: 4, drafts_created: 31, in_person_interview_skipped: 3,
      manual_review: 2, errors: 0, latest_resume_selected: "databricks_resume.pdf",
      sent_count: 0, submitted_count: 0, interview_count: 0, rejected_count: 0,
      withdrawn_count: 0, on_hold_count: 0, applications_this_week: 0,
      applications_this_month: 0, interviews_this_month: 0, recruiters_contacted: 0,
      interview_rate: null,
    });

    renderDashboard();

    await waitFor(() => expect(screen.getByText("100")).toBeInTheDocument());
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getAllByText("31").length).toBeGreaterThan(0);
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText(/databricks_resume\.pdf/)).toBeInTheDocument();
  });

  it("shows an error banner when the stats request fails", async () => {
    api.dashboardStats.mockRejectedValue(new Error("network error"));
    renderDashboard();
    await waitFor(() => expect(screen.getByText(/network error/i)).toBeInTheDocument());
  });

  it("shows top recruiters ranked by opportunity volume", async () => {
    api.dashboardStats.mockResolvedValue({
      emails_processed: 0, job_opportunities: 0, replies_skipped: 0, duplicates_skipped: 0,
      drafts_created: 0, in_person_interview_skipped: 0, manual_review: 0, errors: 0,
    });
    api.topRecruiters.mockResolvedValue([
      { id: 1, display_name: "Naveen Gangupamu", company: "PAMTEN", opportunities_count: 7, drafts_count: 5, skipped_count: 2 },
    ]);

    renderDashboard();
    await waitFor(() => expect(screen.getByText("Naveen Gangupamu")).toBeInTheDocument());
    expect(screen.getByText("PAMTEN")).toBeInTheDocument();
  });

  it("shows tracker KPIs distinguishing draft/sent/submitted/interview", async () => {
    api.dashboardStats.mockResolvedValue({
      emails_processed: 5, job_opportunities: 5, replies_skipped: 0, duplicates_skipped: 0,
      drafts_created: 5, in_person_interview_skipped: 0, manual_review: 0, errors: 0,
      sent_count: 3, submitted_count: 2, interview_count: 1, rejected_count: 0,
      withdrawn_count: 0, on_hold_count: 0, applications_this_week: 4,
      applications_this_month: 5, interviews_this_month: 1, recruiters_contacted: 3,
      interview_rate: 50.0,
    });

    renderDashboard();
    await waitFor(() => expect(screen.getByText("Sent")).toBeInTheDocument());
    expect(screen.getByText("Submitted")).toBeInTheDocument();
    expect(screen.getByText("Interviews")).toBeInTheDocument();
    expect(screen.getByText("50%")).toBeInTheDocument();
  });

  it("never shows a misleading interview rate percentage when nothing has been submitted", async () => {
    api.dashboardStats.mockResolvedValue({
      emails_processed: 0, job_opportunities: 0, replies_skipped: 0, duplicates_skipped: 0,
      drafts_created: 0, in_person_interview_skipped: 0, manual_review: 0, errors: 0,
      sent_count: 0, submitted_count: 0, interview_count: 0, rejected_count: 0,
      withdrawn_count: 0, on_hold_count: 0, applications_this_week: 0,
      applications_this_month: 0, interviews_this_month: 0, recruiters_contacted: 0,
      interview_rate: null,
    });
    renderDashboard();
    await waitFor(() => expect(screen.getByText("Interview rate")).toBeInTheDocument());
    expect(screen.getByText("n/a")).toBeInTheDocument();
  });
});
