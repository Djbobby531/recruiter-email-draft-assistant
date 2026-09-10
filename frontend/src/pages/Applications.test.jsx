import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Applications from "./Applications.jsx";
import { api } from "../api.js";

vi.mock("../api.js", () => ({
  api: {
    listApplications: vi.fn(),
    getApplication: vi.fn(),
    resolveReview: vi.fn(),
    skipApplication: vi.fn(),
    updateApplicationStatus: vi.fn(),
    syncSentApplications: vi.fn(),
    applicationsExportUrl: vi.fn(() => "/api/applications/export.csv"),
    applicationResumeFileUrl: vi.fn((id) => `/api/applications/${id}/resume-file`),
  },
}));

const MANUAL_REVIEW_APP = {
  id: 1, job_title: "Senior Data Engineer (Databricks)", job_location: "Remote",
  local_requirement: "UNKNOWN", implementation_partner: null, end_client: null,
  recruiter_name: null, recruiter_email: null, cc_email: "james@algebrait.com",
  requirements: ["databricks", "python"], selected_resume_id: null, match_score: null,
  match_explanation: null, recruiter_confidence: 0.2,
  generated_subject: null, generated_body: null,
  review_reason: "low recruiter-email confidence: no candidate recruiter email found",
  candidate_recruiter_emails: [], status: "MANUAL_REVIEW", interview_status: "NOT_STARTED",
  created_at: "2026-09-04T10:00:00Z",
};

const DRAFT_APP = {
  id: 2, job_title: "AWS Data Engineer", job_location: "Dallas, TX",
  local_requirement: "NO", implementation_partner: "ABC Staffing", end_client: "XYZ Corp",
  recruiter_name: "John Smith", recruiter_email: "john@abc.com", cc_email: "john@abc.com",
  requirements: ["aws"], selected_resume_id: 5, match_score: 88, match_explanation: "good match",
  recruiter_confidence: 0.9, generated_subject: "Application – AWS Data Engineer – Dallas, TX",
  generated_body: "Hi John,\n\nI came across...", review_reason: null,
  candidate_recruiter_emails: ["john@abc.com"], status: "DRAFT", interview_status: "NOT_STARTED",
  sent_at: null, submitted_at: null, sent_message_id: null, thread_id: "thread-2",
  recruiter: { id: 9, display_name: "John Smith", normalized_email: "john@abc.com", phone: "555-123-4567", company: "ABC Staffing" },
  draft: { gmail_draft_id: "draft-abc", subject: "Application – AWS Data Engineer – Dallas, TX", attached_resume_filename: "aws_resume.pdf" },
  events: [{ id: 1, event_type: "DRAFT_CREATED", event_timestamp: "2026-09-04T10:05:00Z", event_metadata: {} }],
  created_at: "2026-09-04T10:00:00Z",
};

describe("Applications", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listApplications.mockResolvedValue([MANUAL_REVIEW_APP]);
    api.applicationsExportUrl.mockReturnValue("/api/applications/export.csv");
  });

  it("shows a MANUAL REVIEW badge for applications missing a recruiter email", async () => {
    render(<Applications />);
    await waitFor(() => expect(screen.getByText(/Senior Data Engineer \(Databricks\)/)).toBeInTheDocument());
    expect(screen.getByText("MANUAL REVIEW", { selector: ".badge" })).toBeInTheDocument();
  });

  it("lets the user resolve a manual review by typing the correct recruiter email", async () => {
    const user = userEvent.setup();
    api.getApplication.mockResolvedValue(MANUAL_REVIEW_APP);
    api.resolveReview.mockResolvedValue({
      id: 10, gmail_draft_id: null, application_id: 1, to_email: "naveen.gangupamu@pamten.com",
      cc_email: "james@algebrait.com", subject: "Application – Senior Data Engineer (Databricks)",
      attached_resume_filename: "databricks_resume.pdf", status: "CREATED", created_at: "2026-09-04T10:05:00Z",
    });

    render(<Applications />);
    await waitFor(() => expect(screen.getByText(/Senior Data Engineer \(Databricks\)/)).toBeInTheDocument());

    await user.click(screen.getByText(/Senior Data Engineer \(Databricks\)/));

    const emailInput = await screen.findByLabelText(/Recruiter email \(TO\)/i);
    await user.type(emailInput, "naveen.gangupamu@pamten.com");

    await user.click(screen.getByRole("button", { name: /Create Draft With This Recruiter/i }));

    await waitFor(() =>
      expect(api.resolveReview).toHaveBeenCalledWith(1, {
        recruiter_email: "naveen.gangupamu@pamten.com",
        recruiter_name: null,
      })
    );
  });

  it("never shows a Send button anywhere on the page", async () => {
    render(<Applications />);
    await waitFor(() => expect(screen.getByText(/Senior Data Engineer \(Databricks\)/)).toBeInTheDocument());
    const sendButtons = screen.queryAllByRole("button", { name: /^send$/i });
    expect(sendButtons).toHaveLength(0);
  });

  it("filters applications via the search box", async () => {
    const user = userEvent.setup();
    render(<Applications />);
    await waitFor(() => expect(api.listApplications).toHaveBeenCalled());

    await user.type(screen.getByPlaceholderText(/search recruiter/i), "PAMTEN");
    await waitFor(() =>
      expect(api.listApplications).toHaveBeenLastCalledWith(expect.objectContaining({ search: "PAMTEN" }))
    );
  });

  it("shows an 'Awaiting send confirmation' banner for a DRAFT application, never claiming it was sent", async () => {
    const user = userEvent.setup();
    api.listApplications.mockResolvedValue([DRAFT_APP]);
    api.getApplication.mockResolvedValue(DRAFT_APP);

    render(<Applications />);
    await waitFor(() => expect(screen.getByText("AWS Data Engineer")).toBeInTheDocument());
    await user.click(screen.getByText("AWS Data Engineer"));

    await waitFor(() => expect(screen.getByText(/Awaiting send confirmation/i)).toBeInTheDocument());
    expect(screen.queryByText(/^Sent$/)).not.toBeInTheDocument();
  });

  it("renders the application timeline from real event data", async () => {
    const user = userEvent.setup();
    api.listApplications.mockResolvedValue([DRAFT_APP]);
    api.getApplication.mockResolvedValue(DRAFT_APP);

    render(<Applications />);
    await waitFor(() => expect(screen.getByText("AWS Data Engineer")).toBeInTheDocument());
    await user.click(screen.getByText("AWS Data Engineer"));

    await waitFor(() => expect(screen.getByText("Gmail draft created")).toBeInTheDocument());
  });

  it("applies a valid status transition without requiring an override", async () => {
    const user = userEvent.setup();
    api.listApplications.mockResolvedValue([DRAFT_APP]);
    api.getApplication.mockResolvedValue(DRAFT_APP);
    api.updateApplicationStatus.mockResolvedValue({ ...DRAFT_APP, status: "SENT" });

    render(<Applications />);
    await waitFor(() => expect(screen.getByText("AWS Data Engineer")).toBeInTheDocument());
    await user.click(screen.getByText("AWS Data Engineer"));
    await waitFor(() => expect(screen.getByText("Change Status")).toBeInTheDocument());

    await user.selectOptions(screen.getByDisplayValue("Select a status..."), "SENT");
    await user.click(screen.getByRole("button", { name: /^apply$/i }));

    await waitFor(() =>
      expect(api.updateApplicationStatus).toHaveBeenCalledWith(2, { status: "SENT", override: false })
    );
  });

  it("requires the override checkbox for a non-standard transition", async () => {
    const user = userEvent.setup();
    api.listApplications.mockResolvedValue([DRAFT_APP]);
    api.getApplication.mockResolvedValue(DRAFT_APP);

    render(<Applications />);
    await waitFor(() => expect(screen.getByText("AWS Data Engineer")).toBeInTheDocument());
    await user.click(screen.getByText("AWS Data Engineer"));
    await waitFor(() => expect(screen.getByText("Change Status")).toBeInTheDocument());

    await user.selectOptions(screen.getByDisplayValue("Select a status..."), "INTERVIEW");
    expect(screen.getByRole("button", { name: /^apply$/i })).toBeDisabled();
    expect(screen.getByText(/override lifecycle rules/i)).toBeInTheDocument();
  });

  it("exposes a CSV export link", async () => {
    render(<Applications />);
    await waitFor(() => expect(screen.getByText(/Senior Data Engineer \(Databricks\)/)).toBeInTheDocument());
    const link = screen.getByRole("link", { name: /export csv/i });
    expect(link).toHaveAttribute("href", "/api/applications/export.csv");
  });

  it("triggers a sent-status sync when the user clicks 'Check for Sent Emails'", async () => {
    const user = userEvent.setup();
    api.syncSentApplications.mockResolvedValue({ updated: 1 });
    render(<Applications />);
    await waitFor(() => expect(screen.getByText(/Senior Data Engineer \(Databricks\)/)).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /check for sent emails/i }));
    await waitFor(() => expect(api.syncSentApplications).toHaveBeenCalled());
  });
});
