import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "../axe";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SignInForm, validate } from "@/components/auth/sign-in-form";

const magicLink = vi.fn();
const social = vi.fn();
const email = vi.fn();
const signUpEmail = vi.fn();
const replace = vi.fn();
const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace, refresh }) }));
vi.mock("@/lib/auth-client", () => ({
  authClient: {
    signIn: {
      magicLink: (...a: unknown[]) => magicLink(...a),
      social: (...a: unknown[]) => social(...a),
      email: (...a: unknown[]) => email(...a),
    },
    signUp: { email: (...a: unknown[]) => signUpEmail(...a) },
  },
}));

beforeEach(() => {
  for (const fn of [replace, refresh]) fn.mockReset();
  magicLink.mockReset().mockResolvedValue({ data: { status: true }, error: null });
  social.mockReset().mockResolvedValue({ data: null, error: null });
  email.mockReset().mockResolvedValue({ data: {}, error: null });
  signUpEmail.mockReset().mockResolvedValue({ data: {}, error: null });
});

const form = (props: Partial<React.ComponentProps<typeof SignInForm>> = {}) =>
  render(<SignInForm google="on" linkDelivery="email" expiresInMinutes={10} {...props} />);

const linkButton = () => screen.getByRole("button", { name: "Email me a sign-in link" });
const sendButton = () => screen.getByRole("button", { name: "Send sign-in link" });
const keyInput = () => screen.getByLabelText("Your Resend API key");
const KEY = "re_test_0123456789abcdef";

describe("SignInForm: password sign-in", () => {
  it("is accessible and labels fields for password managers", async () => {
    const { container } = form();
    const input = screen.getByLabelText("Email address");
    expect(input).toHaveAttribute("type", "email");
    expect(input).toHaveAttribute("autocomplete", "email");
    expect(input).toHaveAttribute("spellcheck", "false");
    expect(screen.getByLabelText("Password")).toHaveAttribute("autocomplete", "current-password");
    expect(await axe(container)).toHaveNoViolations();
  });

  it("signs in with email and password on Enter, then enters the app", async () => {
    const user = userEvent.setup();
    form();
    await user.type(screen.getByLabelText("Email address"), "  ada@example.com ");
    await user.type(screen.getByLabelText("Password"), "correct horse{Enter}");
    expect(email).toHaveBeenCalledWith({ email: "ada@example.com", password: "correct horse" });
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
    expect(refresh).toHaveBeenCalled();
    expect(magicLink).not.toHaveBeenCalled();
  });

  it("asks for a missing password and focuses it", async () => {
    const user = userEvent.setup();
    form();
    await user.type(screen.getByLabelText("Email address"), "ada@example.com{Enter}");
    const password = screen.getByLabelText("Password");
    expect(password).toHaveFocus();
    expect(password).toHaveAccessibleDescription("Enter your password.");
    expect(email).not.toHaveBeenCalled();
  });

  it("explains a wrong password without saying which part was wrong", async () => {
    email.mockResolvedValue({ data: null, error: { status: 401 } });
    const user = userEvent.setup();
    form();
    await user.type(screen.getByLabelText("Email address"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "nope{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("email and password don’t match");
    expect(replace).not.toHaveBeenCalled();
  });

  it("says when the server cannot be reached instead of doing nothing", async () => {
    email.mockRejectedValue(new TypeError("Failed to fetch"));
    const user = userEvent.setup();
    form();
    await user.type(screen.getByLabelText("Email address"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "secret{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("couldn’t reach the server");
  });

  it("explains a sign-in refused because of the web address", async () => {
    email.mockResolvedValue({ data: null, error: { status: 403, code: "INVALID_ORIGIN" } });
    const user = userEvent.setup();
    form();
    await user.type(screen.getByLabelText("Email address"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "secret{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "isn’t allowed from this web address",
    );
  });

  it("explains a server failure", async () => {
    email.mockResolvedValue({ data: null, error: { status: 500 } });
    const user = userEvent.setup();
    form();
    await user.type(screen.getByLabelText("Email address"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "secret{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("server ran into a problem");
  });

  it("posts the form, so credentials never go into the address bar", () => {
    const { container } = form();
    expect(container.querySelector("form")).toHaveAttribute("method", "post");
  });

  it("links to registration", () => {
    form();
    expect(screen.getByRole("link", { name: "Create an account" })).toHaveAttribute(
      "href",
      "/sign-in?mode=register",
    );
  });
});

describe("SignInForm: registration", () => {
  it("is accessible, asks for a name and a new password, and hides the link option", async () => {
    const { container } = form({ mode: "register" });
    expect(screen.getByLabelText("Name")).toHaveAttribute("autocomplete", "name");
    const password = screen.getByLabelText("Password");
    expect(password).toHaveAttribute("autocomplete", "new-password");
    expect(password).toHaveAccessibleDescription("At least 8 characters.");
    expect(screen.queryByRole("button", { name: /sign-in link/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/sign-in");
    expect(await axe(container)).toHaveNoViolations();
  });

  it("validates every field and focuses the first problem", async () => {
    const user = userEvent.setup();
    form({ mode: "register" });
    await user.type(screen.getByLabelText("Password"), "short");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(screen.getByLabelText("Name")).toHaveFocus();
    expect(screen.getByLabelText("Email address")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Password")).toHaveAccessibleDescription(
      /Use at least 8 characters/,
    );
    expect(signUpEmail).not.toHaveBeenCalled();
  });

  it("creates the account and enters the app", async () => {
    const user = userEvent.setup();
    form({ mode: "register" });
    await user.type(screen.getByLabelText("Name"), " Ada Lovelace ");
    await user.type(screen.getByLabelText("Email address"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "analytical engine");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(signUpEmail).toHaveBeenCalledWith({
      name: "Ada Lovelace",
      email: "ada@example.com",
      password: "analytical engine",
    });
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
  });

  it("says when the email is already registered", async () => {
    signUpEmail.mockResolvedValue({
      data: null,
      error: { status: 422, code: "USER_ALREADY_EXISTS_USE_ANOTHER_EMAIL" },
    });
    const user = userEvent.setup();
    form({ mode: "register" });
    await user.type(screen.getByLabelText("Name"), "Ada");
    await user.type(screen.getByLabelText("Email address"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "analytical engine{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("already exists");
  });
});

describe("SignInForm: sign-in link", () => {
  it("validates the email only, focuses it and announces the error", async () => {
    const user = userEvent.setup();
    form();
    await user.type(screen.getByLabelText("Email address"), "not-an-email");
    await user.click(linkButton());

    const input = screen.getByLabelText("Email address");
    expect(input).toHaveFocus();
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription(/Enter your email address/);
    expect(screen.getByLabelText("Password")).not.toHaveAttribute("aria-invalid");
    expect(magicLink).not.toHaveBeenCalled();
  });

  it("trims and sends the link without a password", async () => {
    const user = userEvent.setup();
    form();
    expect(linkButton()).not.toHaveAttribute("aria-disabled");
    await user.type(screen.getByLabelText("Email address"), "  ada@example.com  ");
    await user.click(linkButton());

    expect(magicLink).toHaveBeenCalledWith({
      email: "ada@example.com",
      callbackURL: "/",
      errorCallbackURL: "/sign-in",
    });
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("Check your inbox");
    expect(status).toHaveTextContent("ada@example.com");
    expect(status).toHaveTextContent(/expires in 10\sminutes/);
  });

  it("explains rate limiting", async () => {
    magicLink.mockResolvedValue({ data: null, error: { status: 429 } });
    const user = userEvent.setup();
    form();
    await user.type(screen.getByLabelText("Email address"), "ada@example.com");
    await user.click(linkButton());
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts");
  });

  it("lets the user go back from the sent state", async () => {
    const user = userEvent.setup();
    form();
    await user.type(screen.getByLabelText("Email address"), "ada@example.com");
    await user.click(linkButton());
    await user.click(await screen.findByRole("button", { name: "Use a different email" }));
    await waitFor(() => expect(screen.getByLabelText("Email address")).toBeInTheDocument());
  });

  it("labels Resend's test sender as testing only and explains its limit", async () => {
    magicLink.mockResolvedValue({ data: null, error: { status: 500 } });
    const user = userEvent.setup();
    form({ linkDelivery: "resend-test" });
    expect(linkButton()).toHaveAccessibleDescription(/Testing only.*Resend account owner/);
    await user.type(screen.getByLabelText("Email address"), "someone@example.com");
    await user.click(linkButton());
    await user.click(sendButton());
    expect(await screen.findByRole("alert")).toHaveTextContent("only reach the Resend account");
  });

  it("has no testing note when real email is configured", () => {
    form({ linkDelivery: "email" });
    expect(screen.queryByRole("button", { name: "About sign-in links" })).not.toBeInTheDocument();
    expect(linkButton()).not.toHaveAccessibleDescription();
  });

  describe("while links are only printed to the server log", () => {
    it("describes the button as testing only, and passes axe with the note open", async () => {
      const user = userEvent.setup();
      const { container } = form({ linkDelivery: "log" });
      expect(linkButton()).toHaveAccessibleDescription(/Testing only.*Resend API key/);
      expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

      await user.hover(linkButton());
      expect(screen.getByRole("tooltip")).toBeVisible();
      expect(await axe(container)).toHaveNoViolations();
      await user.unhover(linkButton());
      expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
      await user.hover(linkButton());
      await user.keyboard("{Escape}");
      expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    });

    it("opens only on hover: not on keyboard focus, and the info icon is no button", async () => {
      const user = userEvent.setup();
      form({ linkDelivery: "log" });
      expect(screen.queryByRole("button", { name: "About sign-in links" })).not.toBeInTheDocument();
      act(() => linkButton().focus());
      expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
      await user.hover(linkButton());
      expect(screen.getByRole("tooltip")).toHaveTextContent("No email is sent yet");
      await user.unhover(linkButton());
      expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    });

    it("says where the link went instead of claiming an email was sent", async () => {
      const user = userEvent.setup();
      form({ linkDelivery: "log" });
      await user.type(screen.getByLabelText("Email address"), "ada@example.com");
      await user.click(linkButton());
      await user.click(sendButton());
      expect(magicLink).toHaveBeenCalledWith({
        email: "ada@example.com",
        callbackURL: "/",
        errorCallbackURL: "/sign-in",
      });
      const status = await screen.findByRole("status");
      expect(status).toHaveTextContent("Check the server terminal");
      expect(status).toHaveTextContent("no email was sent");
    });
  });
});

describe("SignInForm: the tester's own Resend API key (testing mode)", () => {
  async function openKeyStep(delivery: "log" | "resend-test" = "log") {
    const user = userEvent.setup();
    const view = form({ linkDelivery: delivery });
    await user.type(screen.getByLabelText("Email address"), "ada@example.com");
    await user.click(linkButton());
    return { user, ...view };
  }

  it("asks for a key with the testing notice instead of sending, and passes axe", async () => {
    const { container } = await openKeyStep();
    expect(magicLink).not.toHaveBeenCalled();
    const group = screen.getByRole("group", { name: "Email me a sign-in link" });
    expect(group).toHaveAccessibleDescription(
      /this is just for the testing version.*A registered domain is needed for sending email to any user/i,
    );
    expect(keyInput()).toHaveFocus();
    expect(keyInput()).toHaveAttribute("type", "password");
    expect(keyInput()).toHaveAttribute("autocomplete", "off");
    expect(keyInput()).toHaveAccessibleDescription(/never stored.*terminal/);
    expect(await axe(container)).toHaveNoViolations();
  });

  it("sends the link with the key, trimmed, and says it went to an inbox", async () => {
    const { user } = await openKeyStep();
    await user.type(keyInput(), `  ${KEY} `);
    await user.click(sendButton());
    expect(magicLink).toHaveBeenCalledWith({
      email: "ada@example.com",
      callbackURL: "/",
      errorCallbackURL: "/sign-in",
      metadata: { resendApiKey: KEY },
    });
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("Check your inbox");
    expect(status).toHaveTextContent("only arrives if this address owns the Resend account");
  });

  it("sends on Enter in the key field, not the password sign-in", async () => {
    const { user } = await openKeyStep();
    await user.type(keyInput(), `${KEY}{Enter}`);
    expect(magicLink).toHaveBeenCalledTimes(1);
    expect(email).not.toHaveBeenCalled();
  });

  it("rejects something that is not a Resend key before sending", async () => {
    const { user } = await openKeyStep();
    await user.type(keyInput(), "sk_live_nope");
    await user.click(sendButton());
    expect(magicLink).not.toHaveBeenCalled();
    expect(keyInput()).toHaveFocus();
    expect(keyInput()).toHaveAccessibleDescription(/starts with re_/);
  });

  it("shows a key Resend refused on the key field", async () => {
    magicLink.mockResolvedValue({ data: null, error: { status: 400, code: "RESEND_KEY_INVALID" } });
    const { user } = await openKeyStep();
    await user.type(keyInput(), KEY);
    await user.click(sendButton());
    await waitFor(() => expect(keyInput()).toHaveFocus());
    expect(keyInput()).toHaveAttribute("aria-invalid", "true");
    expect(keyInput()).toHaveAccessibleDescription(/didn’t accept this API key/);
  });

  it("explains the test sender's recipient limit", async () => {
    magicLink.mockResolvedValue({
      data: null,
      error: { status: 400, code: "RESEND_RECIPIENT_NOT_ALLOWED" },
    });
    const { user } = await openKeyStep();
    await user.type(keyInput(), KEY);
    await user.click(sendButton());
    expect(await screen.findByRole("alert")).toHaveTextContent("owns the Resend account");
  });

  it("offers the server's own key as the fallback when it has one", async () => {
    await openKeyStep("resend-test");
    expect(keyInput()).toHaveAccessibleDescription(/this server’s own key/);
  });

  it("closes with Cancel or Escape and returns focus to the link button", async () => {
    const { user } = await openKeyStep();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(linkButton()).toHaveFocus());
    await user.click(linkButton());
    await waitFor(() => expect(keyInput()).toHaveFocus());
    await user.keyboard("{Escape}");
    await waitFor(() => expect(linkButton()).toHaveFocus());
    expect(magicLink).not.toHaveBeenCalled();
  });
});

describe("SignInForm: sign-in links switched off", () => {
  it("keeps the button visible but inert, and says why only on hover", async () => {
    const user = userEvent.setup();
    const { container } = form({ linkDelivery: "off" });
    expect(linkButton()).toHaveAttribute("aria-disabled", "true");
    expect(linkButton()).not.toBeDisabled(); // still focusable, so the reason can be read
    await user.type(screen.getByLabelText("Email address"), "a@b.co");
    await user.click(linkButton());
    expect(magicLink).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Your Resend API key")).not.toBeInTheDocument();
    await user.hover(linkButton());
    expect(screen.getByRole("tooltip")).toHaveTextContent("Not available yet");
    expect(screen.getByRole("tooltip")).toHaveTextContent("password or Google");
    expect(await axe(container)).toHaveNoViolations();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    await user.unhover(linkButton());
    act(() => linkButton().focus()); // focus alone never opens it
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    expect(linkButton()).toHaveAccessibleDescription(/Not available yet/);
  });
});

describe("SignInForm: Google and link errors", () => {
  it("signs in with Google when it is configured", async () => {
    form({ google: "on" });
    const google = screen.getByRole("button", { name: "Continue with Google" });
    expect(google).not.toHaveAccessibleDescription();
    await userEvent.setup().click(google);
    expect(social).toHaveBeenCalledWith({ provider: "google", callbackURL: "/" });
  });

  it("shows Google disabled, with setup steps, until it is configured", async () => {
    const { container } = form({ google: "setup-needed" });
    const google = screen.getByRole("button", { name: "Continue with Google" });
    expect(google).toBeDisabled();
    expect(google).toHaveAccessibleDescription(/GOOGLE_CLIENT_ID.*GOOGLE_CLIENT_SECRET/);
    expect(await axe(container)).toHaveNoViolations();
  });

  it("hides Google entirely when it is off, and the link option still follows 'or'", () => {
    form({ google: "off" });
    expect(screen.queryByRole("button", { name: "Continue with Google" })).not.toBeInTheDocument();
    expect(screen.getByText("or")).toBeInTheDocument();
    expect(linkButton()).toBeInTheDocument();
    cleanup();
    form({ google: "off", mode: "register" });
    expect(screen.queryByText("or")).not.toBeInTheDocument();
  });

  it("puts Google, then the sign-in link, after 'or', below the password sign-in", () => {
    form({ google: "on" });
    const order = screen
      .getAllByRole("button")
      .map((b) => b.textContent)
      .filter((t) => t && /Sign in|Google|sign-in link/.test(t));
    expect(order).toEqual(["Sign in", "Continue with Google", "Email me a sign-in link"]);
  });

  it("shows why a link failed", () => {
    form({ initialError: "That sign-in link has expired. Request a new one below." });
    expect(screen.getByRole("alert")).toHaveTextContent("has expired");
  });
});

describe("validate", () => {
  const ok = { name: "Ada", email: "ada@example.com", password: "12345678" };
  it("needs only a valid email for a sign-in link", () => {
    expect(validate("sign-in", "link", { ...ok, password: "" })).toEqual({});
  });
  it("accepts a password of exactly the minimum length at registration", () => {
    expect(validate("register", "password", ok)).toEqual({});
    expect(validate("register", "password", { ...ok, password: "1234567" })).toHaveProperty(
      "password",
    );
  });
  it("never asks for a name when signing in", () => {
    expect(validate("sign-in", "password", { ...ok, name: "" })).toEqual({});
  });
});
