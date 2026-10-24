import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "../axe";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ForgotPasswordForm } from "@/components/auth/forgot-password-form";
import { ResetPasswordForm } from "@/components/auth/reset-password-form";

const requestPasswordReset = vi.fn();
const resetPassword = vi.fn();
const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("@/lib/auth-client", () => ({
  authClient: {
    requestPasswordReset: (...a: unknown[]) => requestPasswordReset(...a),
    resetPassword: (...a: unknown[]) => resetPassword(...a),
  },
}));

beforeEach(() => {
  requestPasswordReset.mockReset().mockResolvedValue({ data: { status: true }, error: null });
  resetPassword.mockReset().mockResolvedValue({ data: { status: true }, error: null });
  replace.mockReset();
});

const SAME_MESSAGE = "If an account uses ada@example.com, we’ve emailed a link";

describe("ForgotPasswordForm", () => {
  const forgot = (toLog = false) =>
    render(<ForgotPasswordForm expiresInMinutes={30} toLog={toLog} />);

  it("is accessible", async () => {
    const { container } = forgot();
    expect(screen.getByLabelText("Email address")).toHaveAttribute("autocomplete", "email");
    expect(await axe(container)).toHaveNoViolations();
  });

  it("asks for a valid address before sending anything", async () => {
    const user = userEvent.setup();
    forgot();
    await user.type(screen.getByLabelText("Email address"), "not-an-email{Enter}");
    expect(screen.getByLabelText("Email address")).toHaveFocus();
    expect(screen.getByLabelText("Email address")).toHaveAccessibleDescription(
      "Enter your email address, like name@company.com.",
    );
    expect(requestPasswordReset).not.toHaveBeenCalled();
  });

  it("requests a link that lands on the reset page", async () => {
    const user = userEvent.setup();
    forgot();
    await user.type(screen.getByLabelText("Email address"), " ada@example.com {Enter}");
    expect(requestPasswordReset).toHaveBeenCalledWith({
      email: "ada@example.com",
      redirectTo: "/sign-in/reset",
    });
    expect(await screen.findByRole("status")).toHaveTextContent(SAME_MESSAGE);
    expect(screen.getByRole("status")).toHaveTextContent("It works for 30 minutes");
  });

  it.each([
    ["the server says no", { data: null, error: { status: 400, code: "USER_NOT_FOUND" } }],
    ["the server fails", { data: null, error: { status: 422 } }],
  ])("shows the same message when %s, so it never tells who has an account", async (_, answer) => {
    requestPasswordReset.mockResolvedValue(answer);
    const user = userEvent.setup();
    forgot();
    await user.type(screen.getByLabelText("Email address"), "ada@example.com{Enter}");
    expect(await screen.findByRole("status")).toHaveTextContent(SAME_MESSAGE);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("says when nothing was sent because the server was not reached", async () => {
    requestPasswordReset.mockRejectedValue(new TypeError("Failed to fetch"));
    const user = userEvent.setup();
    forgot();
    await user.type(screen.getByLabelText("Email address"), "ada@example.com{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("couldn’t reach the server");
  });

  it("says when there were too many requests", async () => {
    requestPasswordReset.mockResolvedValue({ data: null, error: { status: 429 } });
    const user = userEvent.setup();
    forgot();
    await user.type(screen.getByLabelText("Email address"), "ada@example.com{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts");
  });

  it("points to the server terminal in testing mode", async () => {
    const user = userEvent.setup();
    forgot(true);
    await user.type(screen.getByLabelText("Email address"), "ada@example.com{Enter}");
    expect(await screen.findByRole("status")).toHaveTextContent("printed in the terminal");
  });

  it("lets a different address be tried", async () => {
    const user = userEvent.setup();
    forgot();
    await user.type(screen.getByLabelText("Email address"), "ada@example.com{Enter}");
    await user.click(await screen.findByRole("button", { name: "Use a different email" }));
    expect(screen.getByLabelText("Email address")).toBeInTheDocument();
  });
});

describe("ResetPasswordForm", () => {
  const resetForm = () => render(<ResetPasswordForm token="tok123" />);

  it("is accessible and asks for a new password twice", async () => {
    const { container } = resetForm();
    expect(screen.getByLabelText("New password")).toHaveAttribute("autocomplete", "new-password");
    expect(screen.getByLabelText("Confirm new password")).toHaveAttribute(
      "autocomplete",
      "new-password",
    );
    expect(await axe(container)).toHaveNoViolations();
  });

  it("refuses a weak password, says what is missing and focuses it", async () => {
    const user = userEvent.setup();
    resetForm();
    await user.type(screen.getByLabelText("New password"), "short");
    await user.type(screen.getByLabelText("Confirm new password"), "short{Enter}");
    expect(screen.getByLabelText("New password")).toHaveFocus();
    expect(screen.getByLabelText("New password")).toHaveAccessibleDescription(
      expect.stringContaining("Choose a stronger password: at least 10 characters"),
    );
    expect(resetPassword).not.toHaveBeenCalled();
  });

  it("shows the rules as a live checklist, without the email rule (the address is unknown)", async () => {
    const user = userEvent.setup();
    resetForm();
    const list = screen.getByRole("list", { name: "Password rules" });
    expect(screen.getByLabelText("New password")).toHaveAccessibleDescription(
      expect.stringContaining("At least 10 characters: not yet"),
    );
    expect(list).not.toHaveTextContent("email");
    await user.type(screen.getByLabelText("New password"), "abcDEF");
    expect(list).toHaveTextContent("A lowercase letter: met");
    expect(list).toHaveTextContent("A number: not yet");
  });

  it("shows the server's policy refusal on the field", async () => {
    resetPassword.mockResolvedValue({
      data: null,
      error: {
        status: 400,
        code: "PASSWORD_TOO_WEAK",
        message: "Choose a stronger password: not the part of your email before the @.",
      },
    });
    const user = userEvent.setup();
    resetForm();
    await user.type(screen.getByLabelText("New password"), "Long-enough-Pass1");
    await user.type(screen.getByLabelText("Confirm new password"), "Long-enough-Pass1{Enter}");
    await waitFor(() =>
      expect(screen.getByLabelText("New password")).toHaveAccessibleDescription(
        expect.stringContaining("not the part of your email"),
      ),
    );
  });

  it("refuses passwords that don't match and focuses the confirmation", async () => {
    const user = userEvent.setup();
    resetForm();
    await user.type(screen.getByLabelText("New password"), "Long-enough-Pass1");
    await user.type(screen.getByLabelText("Confirm new password"), "Different-Pass12{Enter}");
    expect(screen.getByLabelText("Confirm new password")).toHaveFocus();
    expect(screen.getByLabelText("Confirm new password")).toHaveAccessibleDescription(
      "The passwords don’t match.",
    );
    expect(resetPassword).not.toHaveBeenCalled();
  });

  it("sets the new password with the link's token, then goes straight to sign-in", async () => {
    const user = userEvent.setup();
    resetForm();
    await user.type(screen.getByLabelText("New password"), "Long-enough-Pass1");
    await user.type(screen.getByLabelText("Confirm new password"), "Long-enough-Pass1");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    expect(resetPassword).toHaveBeenCalledWith({
      newPassword: "Long-enough-Pass1",
      token: "tok123",
    });
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/sign-in?reset=done"));
    // still busy while the sign-in page loads: it cannot be sent twice
    expect(screen.getByRole("button", { name: "Changing password…" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });

  it("explains an expired or used link and offers a new one", async () => {
    resetPassword.mockResolvedValue({ data: null, error: { status: 400, code: "INVALID_TOKEN" } });
    const user = userEvent.setup();
    resetForm();
    await user.type(screen.getByLabelText("New password"), "Long-enough-Pass1");
    await user.type(screen.getByLabelText("Confirm new password"), "Long-enough-Pass1{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("expired or was already used");
    expect(screen.getByRole("link", { name: "Get a new link" })).toHaveAttribute(
      "href",
      "/sign-in/forgot",
    );
  });

  it("says when the server cannot be reached", async () => {
    resetPassword.mockRejectedValue(new TypeError("Failed to fetch"));
    const user = userEvent.setup();
    resetForm();
    await user.type(screen.getByLabelText("New password"), "Long-enough-Pass1");
    await user.type(screen.getByLabelText("Confirm new password"), "Long-enough-Pass1{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("couldn’t reach the server");
  });
});
