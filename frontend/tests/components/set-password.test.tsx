import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ChangePasswordForm } from "@/components/auth/change-password-form";
import { PasswordChecklist } from "@/components/auth/password-checklist";
import { SetPasswordForm } from "@/components/auth/set-password-form";
import { axe } from "../axe";

const setPasswordAction = vi.fn();
vi.mock("@/app/set-password/actions", () => ({
  setPasswordAction: (...a: unknown[]) => setPasswordAction(...a),
}));
const changePassword = vi.fn();
vi.mock("@/lib/auth-client", () => ({
  authClient: { changePassword: (...a: unknown[]) => changePassword(...a) },
}));

const STRONG = "Analytical#Engine9";

beforeEach(() => {
  setPasswordAction.mockReset().mockResolvedValue({});
  changePassword.mockReset().mockResolvedValue({ data: {}, error: null });
});

describe("PasswordChecklist", () => {
  it("marks each rule met or not yet, in words as well as icons", async () => {
    const { container } = render(
      <PasswordChecklist id="rules" password="abcdefghij" email="ada@example.com" />,
    );
    const items = screen.getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual([
      "At least 10 characters: met",
      "A lowercase letter: met",
      "An uppercase letter: not yet",
      "A number: not yet",
      "A symbol, like ! # or %: not yet",
      "No spaces: met",
      "Not the part of your email before the @: met",
    ]);
    expect(await axe(container)).toHaveNoViolations();
  });

  it("leaves out the email rule when the address is unknown", () => {
    render(<PasswordChecklist id="rules" password="" />);
    expect(screen.queryByText(/your email/)).toBeNull();
  });
});

describe("SetPasswordForm", () => {
  const form = () => render(<SetPasswordForm email="ada@example.com" />);
  const submit = () => screen.getByRole("button", { name: "Save password and continue" });

  it("is accessible", async () => {
    const { container } = form();
    const password = screen.getByLabelText("Password");
    expect(password).toHaveAttribute("autocomplete", "new-password");
    expect(password).toHaveAccessibleDescription(expect.stringContaining("At least 10 characters"));
    expect(await axe(container)).toHaveNoViolations();
  });

  it("keeps the button disabled until the password is valid and confirmed", async () => {
    const user = userEvent.setup();
    form();
    expect(submit()).toBeDisabled();
    await user.type(screen.getByLabelText("Password"), STRONG);
    expect(submit()).toBeDisabled(); // not confirmed yet
    await user.type(screen.getByLabelText("Confirm password"), STRONG);
    expect(submit()).toBeEnabled();
  }, 15_000);

  it("stays disabled for a weak password and updates the checklist live", async () => {
    const user = userEvent.setup();
    form();
    await user.type(screen.getByLabelText("Password"), "ada-Lovelace1!");
    await user.type(screen.getByLabelText("Confirm password"), "ada-Lovelace1!");
    expect(
      screen.getByText("Not the part of your email before the @").parentElement,
    ).toHaveAttribute("data-met", "false");
    expect(submit()).toBeDisabled();
    expect(setPasswordAction).not.toHaveBeenCalled();
  });

  it("stays disabled while the passwords don't match", async () => {
    const user = userEvent.setup();
    form();
    await user.type(screen.getByLabelText("Password"), STRONG);
    await user.type(screen.getByLabelText("Confirm password"), `${STRONG}x`);
    expect(submit()).toBeDisabled();
  });

  it("sends a valid password to the server action", async () => {
    const user = userEvent.setup();
    form();
    await user.type(screen.getByLabelText("Password"), STRONG);
    await user.type(screen.getByLabelText("Confirm password"), STRONG);
    await user.click(submit());
    await waitFor(() => expect(setPasswordAction).toHaveBeenCalled());
    const data = setPasswordAction.mock.calls[0]?.[1] as FormData;
    expect(data.get("newPassword")).toBe(STRONG);
    expect(data.get("confirmPassword")).toBe(STRONG);
  });

  it("shows the server's refusal on the field", async () => {
    setPasswordAction.mockResolvedValue({
      error: "Choose a stronger password: a number.",
      field: "newPassword",
    });
    const user = userEvent.setup();
    form();
    await user.type(screen.getByLabelText("Password"), STRONG);
    await user.type(screen.getByLabelText("Confirm password"), STRONG);
    await user.click(submit());
    await waitFor(() =>
      expect(screen.getByLabelText("Password")).toHaveAccessibleDescription(
        expect.stringContaining("Choose a stronger password: a number."),
      ),
    );
  });

  it("shows any other failure as an alert", async () => {
    setPasswordAction.mockResolvedValue({ error: "We couldn’t save your password. Try again." });
    const user = userEvent.setup();
    form();
    await user.type(screen.getByLabelText("Password"), STRONG);
    await user.type(screen.getByLabelText("Confirm password"), STRONG);
    await user.click(submit());
    expect(await screen.findByRole("alert")).toHaveTextContent("couldn’t save your password");
  });
});

describe("ChangePasswordForm", () => {
  it("refuses a new password that breaks the policy, naming what is missing", async () => {
    const user = userEvent.setup();
    render(<ChangePasswordForm email="ada@example.com" />);
    await user.type(screen.getByLabelText("Current password"), "old one");
    await user.type(screen.getByLabelText("New password"), "longbutweak");
    await user.type(screen.getByLabelText("Confirm new password"), "longbutweak{Enter}");
    expect(screen.getByLabelText("New password")).toHaveFocus();
    expect(screen.getByLabelText("New password")).toHaveAccessibleDescription(
      expect.stringContaining("Choose a stronger password: an uppercase letter; a number"),
    );
    expect(changePassword).not.toHaveBeenCalled();
  });

  it("changes a valid password and signs other devices out", async () => {
    const user = userEvent.setup();
    render(<ChangePasswordForm email="ada@example.com" />);
    await user.type(screen.getByLabelText("Current password"), "old one");
    await user.type(screen.getByLabelText("New password"), STRONG);
    await user.type(screen.getByLabelText("Confirm new password"), `${STRONG}{Enter}`);
    expect(changePassword).toHaveBeenCalledWith({
      currentPassword: "old one",
      newPassword: STRONG,
      revokeOtherSessions: true,
    });
    expect(await screen.findByRole("status")).toHaveTextContent("Your password was changed");
    expect(screen.getByLabelText("New password")).toHaveValue("");
  });
});
