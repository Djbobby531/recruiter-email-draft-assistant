import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import SkippedOpportunities from "./SkippedOpportunities.jsx";
import { api } from "../api.js";

vi.mock("../api.js", () => ({
  api: {
    listSkippedOpportunities: vi.fn(),
    getRecruiter: vi.fn(),
    overrideInterviewSkip: vi.fn(),
    deleteOpportunity: vi.fn(),
  },
}));

const SKIPPED_OPP = {
  id: 1, recruiter_id: 1, job_title: "Senior Data Engineer (Databricks)", job_location: "Irvine, CA",
  interview_type: "IN_PERSON", requires_in_person_interview: true, interview_confidence: 0.96,
  interview_reason: "In-person interview requirement detected: onsite interview",
  interview_evidence: "onsite interview", status: "SKIPPED_IN_PERSON_INTERVIEW",
  interview_overridden: false, interview_override_at: null,
  created_at: "2026-09-06T00:00:00Z",
};

function renderPage() {
  return render(
    <MemoryRouter>
      <SkippedOpportunities />
    </MemoryRouter>
  );
}

describe("SkippedOpportunities", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.confirm = vi.fn(() => true);
    api.listSkippedOpportunities.mockResolvedValue([SKIPPED_OPP]);
  });

  it("lists skipped opportunities with interview type and confidence", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText(/Senior Data Engineer \(Databricks\)/)).toBeInTheDocument());
    expect(screen.getByText("IN_PERSON")).toBeInTheDocument();
    expect(screen.getByText("96%")).toBeInTheDocument();
  });

  it("shows reason, evidence, and recruiter link when a skipped opportunity is selected", async () => {
    const user = userEvent.setup();
    api.getRecruiter.mockResolvedValue({ id: 1, display_name: "Naveen Gangupamu", normalized_email: "naveen@pamten.com" });

    renderPage();
    await waitFor(() => expect(screen.getByText(/Senior Data Engineer \(Databricks\)/)).toBeInTheDocument());
    await user.click(screen.getByText(/Senior Data Engineer \(Databricks\)/));

    await waitFor(() => expect(screen.getByText(/onsite interview/i, { selector: "pre" })).toBeInTheDocument());
    expect(screen.getByText("Naveen Gangupamu")).toBeInTheDocument();
    expect(screen.getByText(/SKIPPED — In-person interview required/)).toBeInTheDocument();
  });

  it("never shows an auto-create-draft button without an explicit override click", async () => {
    const user = userEvent.setup();
    api.getRecruiter.mockResolvedValue({ id: 1, display_name: "Naveen Gangupamu", normalized_email: "naveen@pamten.com" });
    renderPage();
    await waitFor(() => expect(screen.getByText(/Senior Data Engineer \(Databricks\)/)).toBeInTheDocument());
    await user.click(screen.getByText(/Senior Data Engineer \(Databricks\)/));

    await waitFor(() => expect(screen.getByText("Override and Process")).toBeInTheDocument());
    // no button that silently creates a draft without the explicit override label
    expect(screen.queryByRole("button", { name: /^create draft$/i })).not.toBeInTheDocument();
  });

  it("calls the override endpoint when the user confirms", async () => {
    const user = userEvent.setup();
    api.getRecruiter.mockResolvedValue({ id: 1, display_name: "Naveen Gangupamu", normalized_email: "naveen@pamten.com" });
    api.overrideInterviewSkip.mockResolvedValue({ ...SKIPPED_OPP, status: "DRAFT_CREATED", interview_overridden: true });

    renderPage();
    await waitFor(() => expect(screen.getByText(/Senior Data Engineer \(Databricks\)/)).toBeInTheDocument());
    await user.click(screen.getByText(/Senior Data Engineer \(Databricks\)/));
    await waitFor(() => expect(screen.getByText("Override and Process")).toBeInTheDocument());

    await user.click(screen.getByText("Override and Process"));
    await waitFor(() => expect(api.overrideInterviewSkip).toHaveBeenCalledWith(1));
  });

  it("deletes a skipped opportunity record on request", async () => {
    const user = userEvent.setup();
    api.getRecruiter.mockResolvedValue({ id: 1, display_name: "Naveen Gangupamu", normalized_email: "naveen@pamten.com" });
    api.deleteOpportunity.mockResolvedValue({ deleted: true });

    renderPage();
    await waitFor(() => expect(screen.getByText(/Senior Data Engineer \(Databricks\)/)).toBeInTheDocument());
    await user.click(screen.getByText(/Senior Data Engineer \(Databricks\)/));
    await waitFor(() => expect(screen.getByText("Delete Record")).toBeInTheDocument());

    await user.click(screen.getByText("Delete Record"));
    await waitFor(() => expect(api.deleteOpportunity).toHaveBeenCalledWith(1));
  });
});
