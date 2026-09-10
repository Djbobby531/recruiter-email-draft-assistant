import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import Recruiters from "./Recruiters.jsx";
import { api } from "../api.js";

vi.mock("../api.js", () => ({
  api: {
    listRecruiters: vi.fn(),
    getRecruiter: vi.fn(),
    getRecruiterOpportunities: vi.fn(),
    updateRecruiter: vi.fn(),
    deleteRecruiter: vi.fn(),
    deleteAllRecruiters: vi.fn(),
  },
}));

const RECRUITER = {
  id: 1, normalized_email: "naveen.gangupamu@pamten.com", display_name: "Naveen Gangupamu",
  company: "PAMTEN", recruiter_role: "Talent Acquisition Executive", phone: "(737) 304-8920",
  opportunities_count: 7, drafts_count: 5, skipped_count: 2,
  first_seen_at: "2026-08-01T00:00:00Z", last_seen_at: "2026-09-06T00:00:00Z", notes: null,
};

function renderPage() {
  return render(
    <MemoryRouter>
      <Recruiters />
    </MemoryRouter>
  );
}

describe("Recruiters", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listRecruiters.mockResolvedValue([RECRUITER]);
  });

  it("lists recruiters with company, email, phone, and opportunity count", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Naveen Gangupamu")).toBeInTheDocument());
    expect(screen.getByText("PAMTEN")).toBeInTheDocument();
    expect(screen.getByText("naveen.gangupamu@pamten.com")).toBeInTheDocument();
    expect(screen.getByText("(737) 304-8920")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
  });

  it("shows recruiter detail with roles and statistics when selected", async () => {
    const user = userEvent.setup();
    api.getRecruiter.mockResolvedValue({
      ...RECRUITER,
      roles: [{ id: 1, job_title: "Senior Data Engineer", opportunity_count: 3, last_seen_at: "2026-09-06T00:00:00Z" }],
    });
    api.getRecruiterOpportunities.mockResolvedValue([]);

    renderPage();
    await waitFor(() => expect(screen.getByText("Naveen Gangupamu")).toBeInTheDocument());
    await user.click(screen.getByText("Naveen Gangupamu"));

    await waitFor(() => expect(screen.getByText("Senior Data Engineer", { exact: false })).toBeInTheDocument());
    expect(screen.getByText("Talent Acquisition Executive")).toBeInTheDocument();
  });

  it("searches recruiters via the search input", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(api.listRecruiters).toHaveBeenCalled());

    await user.type(screen.getByPlaceholderText(/search by name/i), "Naveen");
    await waitFor(() =>
      expect(api.listRecruiters).toHaveBeenLastCalledWith(
        expect.objectContaining({ search: "Naveen" })
      )
    );
  });

  it("toggles the unique mobile numbers filter", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() =>
      expect(api.listRecruiters).toHaveBeenCalledWith(expect.objectContaining({ unique_phone: false }))
    );

    await user.click(screen.getByLabelText(/unique mobile numbers only/i));
    await waitFor(() =>
      expect(api.listRecruiters).toHaveBeenLastCalledWith(expect.objectContaining({ unique_phone: true }))
    );
  });

  it("saves notes for the selected recruiter", async () => {
    const user = userEvent.setup();
    api.getRecruiter.mockResolvedValue({ ...RECRUITER, roles: [] });
    api.getRecruiterOpportunities.mockResolvedValue([]);
    api.updateRecruiter.mockResolvedValue({ ...RECRUITER, notes: "Very responsive" });

    renderPage();
    await waitFor(() => expect(screen.getByText("Naveen Gangupamu")).toBeInTheDocument());
    await user.click(screen.getByText("Naveen Gangupamu"));
    await waitFor(() => expect(screen.getByText("Save Notes")).toBeInTheDocument());

    await user.click(screen.getByText("Save Notes"));
    await waitFor(() => expect(api.updateRecruiter).toHaveBeenCalledWith(1, { notes: "" }));
  });
});
