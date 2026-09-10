import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import CandidateProfile from "./CandidateProfile.jsx";
import { api } from "../api.js";

vi.mock("../api.js", () => ({
  api: {
    getProfile: vi.fn(),
    saveProfile: vi.fn(),
  },
}));

describe("CandidateProfile", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getProfile.mockResolvedValue({
      id: 1, name: "Diwakar Jilakara", experience: "8+ years",
      work_authorization: "Authorized to work in the US", phone: "555-123-4567",
      email: "diwakar@example.com", linkedin: "linkedin.com/in/diwakar",
    });
  });

  it("never renders a current-location form field", async () => {
    render(<CandidateProfile />);
    await waitFor(() => expect(screen.getByDisplayValue("Diwakar Jilakara")).toBeInTheDocument());

    // targets actual form controls (not the privacy disclaimer prose, which
    // legitimately mentions "location" while explaining it is NOT collected)
    expect(screen.queryByLabelText(/location/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/current city/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/current state/i)).not.toBeInTheDocument();
    const allInputs = document.querySelectorAll("input");
    expect(allInputs).toHaveLength(6); // exactly the 6 known candidate-profile fields
  });

  it("saves the profile without ever sending a location field", async () => {
    api.saveProfile.mockResolvedValue({});
    const user = userEvent.setup();
    render(<CandidateProfile />);
    await waitFor(() => expect(screen.getByDisplayValue("Diwakar Jilakara")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /save profile/i }));

    await waitFor(() => expect(api.saveProfile).toHaveBeenCalled());
    const payload = api.saveProfile.mock.calls[0][0];
    expect(payload).not.toHaveProperty("location");
    expect(Object.keys(payload).sort()).toEqual(
      ["email", "experience", "linkedin", "name", "phone", "work_authorization"].sort()
    );
  });
});
