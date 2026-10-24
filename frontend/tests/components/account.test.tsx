import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DeleteAccountForm } from "@/components/auth/delete-account-form";
import { ProfileNameForm } from "@/components/auth/profile-name-form";
import { axe } from "../axe";

const updateUser = vi.fn();
const refresh = vi.fn();
const deleteAccountAction = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh, replace: vi.fn() }) }));
vi.mock("@/lib/auth-client", () => ({
  authClient: { updateUser: (...a: unknown[]) => updateUser(...a) },
}));
vi.mock("@/app/o/[org]/privacy/actions", () => ({
  deleteAccountAction: (...a: unknown[]) => deleteAccountAction(...a),
}));

beforeEach(() => {
  updateUser.mockReset().mockResolvedValue({ data: { status: true }, error: null });
  refresh.mockReset();
  deleteAccountAction.mockReset().mockResolvedValue({});
});

describe("ProfileNameForm", () => {
  const form = () => render(<ProfileNameForm name="Ada" email="ada@example.com" />);

  it("is accessible and shows the email read-only, saying it can't change", async () => {
    const { container } = form();
    const email = screen.getByLabelText("Email address");
    expect(email).toHaveValue("ada@example.com");
    expect(email).toHaveAttribute("readonly");
    expect(email).toHaveAccessibleDescription(/can’t be changed/);
    expect(await axe(container)).toHaveNoViolations();
  });

  it("saves a new name, trimmed, and refreshes the page", async () => {
    const user = userEvent.setup();
    form();
    const name = screen.getByLabelText("Name");
    await user.clear(name);
    await user.type(name, "  Ada King ");
    await user.click(screen.getByRole("button", { name: "Save name" }));
    expect(updateUser).toHaveBeenCalledWith({ name: "Ada King" });
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(refresh).toHaveBeenCalled();
  });

  it("asks for a name instead of saving an empty one", async () => {
    const user = userEvent.setup();
    form();
    await user.clear(screen.getByLabelText("Name"));
    await user.click(screen.getByRole("button", { name: "Save name" }));
    expect(screen.getByLabelText("Name")).toHaveAccessibleDescription("Enter your name.");
    expect(updateUser).not.toHaveBeenCalled();
  });

  it("says when the name wasn't saved", async () => {
    updateUser.mockResolvedValue({ data: null, error: { status: 500 } });
    const user = userEvent.setup();
    form();
    await user.click(screen.getByRole("button", { name: "Save name" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("server ran into a problem");
  });
});

describe("DeleteAccountForm", () => {
  it("asks first, with the safe choice focused, and can be backed out of", async () => {
    const user = userEvent.setup();
    const { container } = render(<DeleteAccountForm />);
    await user.click(screen.getByRole("button", { name: "Delete my account…" }));
    expect(screen.getByRole("button", { name: "Keep my account" })).toHaveFocus();
    expect(screen.getByRole("form", { name: "Confirm deleting your account" })).toHaveTextContent(
      "Enter your password to delete your account and everything in it.",
    );
    expect(await axe(container)).toHaveNoViolations();
    await user.click(screen.getByRole("button", { name: "Keep my account" }));
    expect(screen.getByRole("button", { name: "Delete my account…" })).toBeInTheDocument();
    expect(deleteAccountAction).not.toHaveBeenCalled();
  });

  it("sends the password to the server action", async () => {
    const user = userEvent.setup();
    render(<DeleteAccountForm />);
    await user.click(screen.getByRole("button", { name: "Delete my account…" }));
    await user.type(screen.getByLabelText("Password"), "Str0ng!Passphrase");
    await user.click(screen.getByRole("button", { name: "Delete my account permanently" }));
    await waitFor(() => expect(deleteAccountAction).toHaveBeenCalled());
    expect((deleteAccountAction.mock.calls[0]?.[1] as FormData).get("password")).toBe(
      "Str0ng!Passphrase",
    );
  });

  it("shows a wrong password on the field, and other refusals as an alert", async () => {
    deleteAccountAction
      .mockResolvedValueOnce({ error: "That password isn’t right.", field: "password" })
      .mockResolvedValueOnce({ error: "An analysis is still running. Nothing was deleted." });
    const user = userEvent.setup();
    render(<DeleteAccountForm />);
    await user.click(screen.getByRole("button", { name: "Delete my account…" }));
    await user.type(screen.getByLabelText("Password"), "nope");
    await user.click(screen.getByRole("button", { name: "Delete my account permanently" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Password")).toHaveAccessibleDescription(
        "That password isn’t right.",
      ),
    );
    await user.click(screen.getByRole("button", { name: "Delete my account permanently" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("still running");
  });
});

describe("AccountDeletedNotice", () => {
  it("shows once: the flag leaves the address, so a reload shows nothing", async () => {
    const { AccountDeletedNotice } = await import("@/components/auth/account-deleted-notice");
    window.history.replaceState(null, "", "/sign-in?account=deleted&mode=register");
    render(<AccountDeletedNotice />);
    expect(screen.getByRole("status")).toHaveTextContent("Your account and everything in it");
    await waitFor(() => expect(window.location.search).toBe("?mode=register"));
  });
});
