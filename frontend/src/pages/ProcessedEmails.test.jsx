import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ProcessedEmails from "./ProcessedEmails.jsx";
import { api } from "../api.js";

vi.mock("../api.js", () => ({
  api: {
    listMessages: vi.fn(),
    getMessage: vi.fn(),
    applicationResumeFileUrl: vi.fn((id) => `/api/applications/${id}/resume-file`),
  },
}));

const MESSAGE = {
  id: 1, gmail_message_id: "m1", thread_id: "t1",
  from_email: "naveen@pamten.com", to_email: "diwakar@example.com",
  subject: "Role: Senior Data Engineer", status: "DRAFT_CREATED",
  error_message: null, processed_at: "2026-09-07T10:00:00Z",
};

describe("ProcessedEmails", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listMessages.mockResolvedValue([MESSAGE]);
  });

  it("shows sender and receiver columns", async () => {
    render(<ProcessedEmails />);
    await waitFor(() => expect(screen.getByText(/Senior Data Engineer/)).toBeInTheDocument());
    expect(screen.getByText("naveen@pamten.com")).toBeInTheDocument();
    expect(screen.getByText("diwakar@example.com")).toBeInTheDocument();
  });

  it("filters by status", async () => {
    const user = userEvent.setup();
    render(<ProcessedEmails />);
    await waitFor(() => expect(api.listMessages).toHaveBeenCalled());

    await user.selectOptions(screen.getByDisplayValue("All statuses"), "DRAFT_CREATED");
    await waitFor(() =>
      expect(api.listMessages).toHaveBeenLastCalledWith(expect.objectContaining({ status: "DRAFT_CREATED" }))
    );
  });

  it("searches by subject/sender/recipient", async () => {
    const user = userEvent.setup();
    render(<ProcessedEmails />);
    await waitFor(() => expect(api.listMessages).toHaveBeenCalled());

    await user.type(screen.getByPlaceholderText(/search subject/i), "pamten");
    await waitFor(() =>
      expect(api.listMessages).toHaveBeenLastCalledWith(expect.objectContaining({ search: "pamten" }))
    );
  });

  it("shows the linked application and a link to the resume when a row is clicked", async () => {
    const user = userEvent.setup();
    api.getMessage.mockResolvedValue({
      ...MESSAGE,
      application: {
        id: 5, job_title: "Senior Data Engineer", recruiter_name: "Naveen Gangupamu",
        match_score: 91, selected_resume_id: 3, has_customized_resume: true,
      },
    });

    render(<ProcessedEmails />);
    await waitFor(() => expect(screen.getByText(/Senior Data Engineer/)).toBeInTheDocument());
    await user.click(screen.getByText("naveen@pamten.com"));

    await waitFor(() => expect(screen.getByText("Naveen Gangupamu")).toBeInTheDocument());
    const link = screen.getByRole("link", { name: /view customized resume/i });
    expect(link).toHaveAttribute("href", "/api/applications/5/resume-file");
  });

  it("shows a plain empty state when no application was generated", async () => {
    const user = userEvent.setup();
    api.getMessage.mockResolvedValue({ ...MESSAGE, application: null });

    render(<ProcessedEmails />);
    await waitFor(() => expect(screen.getByText(/Senior Data Engineer/)).toBeInTheDocument());
    await user.click(screen.getByText("naveen@pamten.com"));

    await waitFor(() => expect(screen.getByText(/No application was generated/i)).toBeInTheDocument());
  });
});
