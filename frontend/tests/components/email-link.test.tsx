import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EmailLinkForm, linkError } from "@/components/auth/email-link-form";
import { axe } from "../axe";

const magicLink = vi.fn();
vi.mock("@/lib/auth-client", () => ({
  authClient: { signIn: { magicLink: (...a: unknown[]) => magicLink(...a) } },
}));

beforeEach(() => {
  magicLink.mockReset().mockResolvedValue({ data: { status: true }, error: null });
});

const form = (delivery: "email" | "log" = "email") =>
  render(<EmailLinkForm delivery={delivery} expiresInMinutes={10} />);
const input = () => screen.getByLabelText("Email address");
const send = () => screen.getByRole("button", { name: "Send sign-in link" });

describe("EmailLinkForm", () => {
  it("is accessible and labels the field for password managers", async () => {
    const { container } = form();
    expect(input()).toHaveAttribute("autocomplete", "email");
    expect(await axe(container)).toHaveNoViolations();
  });

  it("checks the address before sending", async () => {
    const user = userEvent.setup();
    form();
    await user.type(input(), "not-an-email");
    await user.click(send());
    expect(input()).toHaveFocus();
    expect(input()).toHaveAccessibleDescription(/Enter your email address/);
    expect(magicLink).not.toHaveBeenCalled();
  });

  it("sends the link and says where it went", async () => {
    const user = userEvent.setup();
    form();
    await user.type(input(), "  owner@example.com {Enter}");
    expect(magicLink).toHaveBeenCalledWith({
      email: "owner@example.com",
      callbackURL: "/",
      errorCallbackURL: "/sign-in",
    });
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("Check your inbox");
    expect(status).toHaveTextContent(/expires in 10\sminutes/);
    await user.click(screen.getByRole("button", { name: "Use a different email" }));
    expect(input()).toBeInTheDocument();
  });

  it("says so when the link only went to the server log", async () => {
    const user = userEvent.setup();
    form("log");
    await user.type(input(), "owner@example.com{Enter}");
    expect(await screen.findByRole("status")).toHaveTextContent("Check the server terminal");
  });

  it("explains an address that can't get links, without naming who can", async () => {
    magicLink.mockResolvedValue({ data: null, error: { status: 403, code: "EMAIL_NOT_ALLOWED" } });
    const user = userEvent.setup();
    const { container } = form();
    await user.type(input(), "someone@example.com{Enter}");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("We can’t send email to this address");
    expect(alert).not.toHaveTextContent(/project owner|testing/i);
    expect(screen.queryByRole("link", { name: /Register/ })).toBeNull();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("tells an address with no account to register first, with the way to do it", async () => {
    magicLink.mockResolvedValue({ data: null, error: { status: 400, code: "ACCOUNT_NOT_FOUND" } });
    const user = userEvent.setup();
    const { container } = form("email");
    await user.type(input(), "stranger@example.com{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "No account found for that email. Register first.",
    );
    expect(screen.getByRole("link", { name: "Register" })).toHaveAttribute(
      "href",
      "/sign-in?mode=register",
    );
    expect(screen.queryByRole("status")).toBeNull();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("explains other failures", async () => {
    magicLink.mockResolvedValue({ data: null, error: { status: 429 } });
    const user = userEvent.setup();
    form();
    await user.type(input(), "owner@example.com{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts");
  });
});

describe("linkError", () => {
  it("names the cause where it can, without test-sender caveats", () => {
    expect(linkError({ status: 400, code: "EMAIL_NOT_CONFIGURED" })).toMatch(/isn’t set up/);
    expect(linkError({ status: 429, code: "EMAIL_RATE_LIMITED" })).toMatch(/busy/);
    for (const error of [{ status: 502 }, { status: 400, code: "RESEND_RECIPIENT_NOT_ALLOWED" }]) {
      expect(linkError(error)).toBe("We couldn’t send the link. Check the address and try again.");
    }
    expect(linkError({})).toMatch(/couldn’t reach the server/);
  });
});
