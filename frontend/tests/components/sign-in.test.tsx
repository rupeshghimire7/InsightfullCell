import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "../axe";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { codeError, registerError, SignInForm, validate } from "@/components/auth/sign-in-form";

const magicLink = vi.fn();
const social = vi.fn();
const email = vi.fn();
const registerEmail = vi.fn();
const sendCode = vi.fn();
const verifyEmail = vi.fn();
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
    register: { email: (...a: unknown[]) => registerEmail(...a) },
    emailCode: { send: (...a: unknown[]) => sendCode(...a) },
    emailOtp: { verifyEmail: (...a: unknown[]) => verifyEmail(...a) },
  },
}));

beforeEach(() => {
  for (const fn of [replace, refresh]) fn.mockReset();
  magicLink.mockReset().mockResolvedValue({ data: { status: true }, error: null });
  social.mockReset().mockResolvedValue({ data: null, error: null });
  email.mockReset().mockResolvedValue({ data: {}, error: null });
  registerEmail.mockReset().mockResolvedValue({ data: { status: true }, error: null });
  sendCode.mockReset().mockResolvedValue({ data: { status: true }, error: null });
  verifyEmail.mockReset().mockResolvedValue({ data: { status: true }, error: null });
});

const form = (props: Partial<React.ComponentProps<typeof SignInForm>> = {}) =>
  render(<SignInForm google="on" linkDelivery="email" {...props} />);

const linkButton = () => screen.getByRole("link", { name: "Email me a sign-in link" });
const offButton = () => screen.getByRole("button", { name: "Email me a sign-in link" });

/** Registers `ada@example.com` and waits for the code step. */
async function reachCodeStep(user: ReturnType<typeof userEvent.setup>) {
  form({ mode: "register" });
  await user.type(screen.getByLabelText("Name"), "Ada");
  await user.type(screen.getByLabelText("Email address"), "ada@example.com{Enter}");
  return screen.findByLabelText("6-digit code");
}

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

describe("SignInForm: sign-in mode", () => {
  it("has no registration fields", () => {
    form({ passwordReset: true });
    expect(screen.queryByLabelText("Name")).toBeNull();
    expect(screen.queryByLabelText("Confirm password")).toBeNull();
    expect(screen.queryByRole("button", { name: "Send me a code" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Sign up with Google" })).toBeNull();
  });

  it("asks an older, unverified account for the emailed code instead of failing", async () => {
    email.mockResolvedValue({ data: null, error: { status: 403, code: "EMAIL_NOT_VERIFIED" } });
    const user = userEvent.setup();
    form();
    await user.type(screen.getByLabelText("Email address"), "old@example.com");
    await user.type(screen.getByLabelText("Password"), "old-password{Enter}");
    expect(await screen.findByLabelText("6-digit code")).toHaveFocus();
    expect(screen.getByRole("status")).toHaveTextContent("confirm your address");
    await user.type(screen.getByLabelText("6-digit code"), "123456{Enter}");
    expect(verifyEmail).toHaveBeenCalledWith({ email: "old@example.com", otp: "123456" });
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
  });
});

describe("SignInForm: registration", () => {
  it("is accessible and asks only for a name and an email, with no password", async () => {
    const { container } = form({ mode: "register" });
    expect(screen.getByLabelText("Name")).toHaveAttribute("autocomplete", "name");
    expect(screen.getByLabelText("Email address")).toHaveAccessibleDescription(
      "We’ll email you a code to confirm it’s yours.",
    );
    expect(screen.queryByLabelText("Password")).toBeNull();
    expect(screen.queryByLabelText("Confirm password")).toBeNull();
    expect(screen.getByRole("button", { name: "Send me a code" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign up with Google" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Email me a sign-in link" })).toBeNull();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/sign-in");
    expect(await axe(container)).toHaveNoViolations();
  });

  it("validates both fields and focuses the first problem", async () => {
    const user = userEvent.setup();
    form({ mode: "register" });
    await user.click(screen.getByRole("button", { name: "Send me a code" }));
    expect(screen.getByLabelText("Name")).toHaveFocus();
    expect(screen.getByLabelText("Email address")).toHaveAttribute("aria-invalid", "true");
    expect(registerEmail).not.toHaveBeenCalled();
  });

  it("emails a code, verifies it, then goes on to create a password", async () => {
    const user = userEvent.setup();
    form({ mode: "register" });
    await user.type(screen.getByLabelText("Name"), " Ada Lovelace ");
    await user.type(screen.getByLabelText("Email address"), "ada@example.com");
    await user.click(screen.getByRole("button", { name: "Send me a code" }));
    expect(registerEmail).toHaveBeenCalledWith({ name: "Ada Lovelace", email: "ada@example.com" });
    const code = await screen.findByLabelText("6-digit code");
    expect(code).toHaveFocus();
    expect(code).toHaveAttribute("autocomplete", "one-time-code");
    expect(code).toHaveAttribute("inputmode", "numeric");
    expect(screen.getByRole("status")).toHaveTextContent("ada@example.com");
    expect(screen.getByRole("status")).toHaveTextContent("expires in 10 minutes");
    await user.type(code, "123 456");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(verifyEmail).toHaveBeenCalledWith({ email: "ada@example.com", otp: "123456" });
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/set-password"));
    expect(refresh).toHaveBeenCalled();
  });

  it("refuses an email that is already registered, with a link to sign in", async () => {
    registerEmail.mockResolvedValue({
      data: null,
      error: { status: 409, code: "EMAIL_ALREADY_REGISTERED" },
    });
    const user = userEvent.setup();
    form({ mode: "register" });
    await user.type(screen.getByLabelText("Name"), "Ada");
    await user.type(screen.getByLabelText("Email address"), "ada@example.com{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "That email is already registered. Sign in instead.",
    );
    expect(screen.getAllByRole("link", { name: "Sign in" })[0]).toHaveAttribute("href", "/sign-in");
    expect(screen.queryByLabelText("6-digit code")).toBeNull();
  });

  it("checks the code's shape before sending it", async () => {
    const user = userEvent.setup();
    const code = await reachCodeStep(user);
    await user.type(code, "12{Enter}");
    expect(code).toHaveAccessibleDescription("Enter the 6-digit code from the email.");
    expect(verifyEmail).not.toHaveBeenCalled();
  });

  it("explains a wrong code and keeps the step open", async () => {
    verifyEmail.mockResolvedValue({ data: null, error: { status: 400, code: "INVALID_OTP" } });
    const user = userEvent.setup();
    const code = await reachCodeStep(user);
    await user.type(code, "000000{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("That code isn’t right");
    expect(replace).not.toHaveBeenCalled();
  });

  it("offers a new code only after the cooldown", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      await reachCodeStep(user);
      const resend = screen.getByRole("button", { name: /Send a new code \(60s\)/ });
      expect(resend).toBeDisabled();
      // One second at a time: each tick schedules the next.
      for (let i = 0; i < 60; i++) act(() => vi.advanceTimersByTime(1000));
      const ready = screen.getByRole("button", { name: "Send a new code" });
      expect(ready).toBeEnabled();
      await user.click(ready);
      expect(sendCode).toHaveBeenCalledWith({ email: "ada@example.com" });
      expect(await screen.findByText(/We sent a new code/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Send a new code \(\d+s\)/ })).toBeDisabled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("goes back to change the address", async () => {
    const user = userEvent.setup();
    await reachCodeStep(user);
    await user.click(screen.getByRole("button", { name: "Use a different email" }));
    expect(screen.getByLabelText("Email address")).toBeInTheDocument();
  });

  it("says the code is in the server terminal in testing mode", async () => {
    const user = userEvent.setup();
    render(<SignInForm google="on" linkDelivery="log" mode="register" />);
    await user.type(screen.getByLabelText("Name"), "Ada");
    await user.type(screen.getByLabelText("Email address"), "ada@example.com{Enter}");
    expect(await screen.findByRole("heading", { name: "Check the server terminal" })).toBeVisible();
  });

  it("offers only Google, with a short note, when email can't be sent", () => {
    form({ mode: "register", linkDelivery: "off" });
    expect(screen.queryByLabelText("Email address")).toBeNull();
    expect(screen.getByText(/isn’t available yet. Sign up with Google instead/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Sign up with Google" })).toBeEnabled();
  });

  it("says registration is unavailable when neither email nor Google is", () => {
    form({ mode: "register", linkDelivery: "off", google: "off" });
    expect(screen.getByText("Registration isn’t available right now.")).toBeVisible();
  });

  it("signs up with Google asking for a new account, and returns errors to registration", async () => {
    form({ mode: "register" });
    await userEvent.setup().click(screen.getByRole("button", { name: "Sign up with Google" }));
    expect(social).toHaveBeenCalledWith({
      provider: "google",
      callbackURL: "/",
      errorCallbackURL: "/sign-in?mode=register",
      requestSignUp: true,
    });
  });
});

describe("SignInForm: sign-in link", () => {
  it("opens the page that asks for the address, without sending anything", async () => {
    form();
    expect(linkButton()).toHaveAttribute("href", "/sign-in/email");
    expect(magicLink).not.toHaveBeenCalled();
  });

  it("carries no caution note in any delivery mode", async () => {
    for (const linkDelivery of ["log", "email"] as const) {
      const user = userEvent.setup();
      form({ linkDelivery });
      expect(linkButton()).not.toHaveAccessibleDescription();
      await user.hover(linkButton());
      expect(screen.queryByRole("tooltip")).toBeNull();
      expect(screen.queryByText(/Testing only/)).toBeNull();
      cleanup();
    }
  });

  it("keeps the button visible but inert while links are off, with no note", async () => {
    const user = userEvent.setup();
    const { container } = form({ linkDelivery: "off" });
    expect(offButton()).toHaveAttribute("aria-disabled", "true");
    expect(screen.queryByRole("link", { name: "Email me a sign-in link" })).toBeNull();
    await user.click(offButton());
    await user.hover(offButton());
    expect(magicLink).not.toHaveBeenCalled();
    expect(screen.queryByRole("tooltip")).toBeNull();
    expect(screen.queryByText(/Not available yet/)).toBeNull();
    expect(await axe(container)).toHaveNoViolations();
  });
});

describe("SignInForm: Google and link errors", () => {
  it("signs in with Google, never asking for a new account, errors back to sign-in", async () => {
    form({ google: "on" });
    const google = screen.getByRole("button", { name: "Continue with Google" });
    expect(google).not.toHaveAccessibleDescription();
    await userEvent.setup().click(google);
    expect(social).toHaveBeenCalledWith({
      provider: "google",
      callbackURL: "/",
      errorCallbackURL: "/sign-in",
    });
  });

  it("shows Google disabled, with setup steps, until it is configured", async () => {
    const { container } = form({ google: "setup-needed" });
    const google = screen.getByRole("button", { name: "Continue with Google" });
    expect(google).toBeDisabled();
    expect(google).toHaveAccessibleDescription(/GOOGLE_CLIENT_ID.*GOOGLE_CLIENT_SECRET/);
    expect(await axe(container)).toHaveNoViolations();
  });

  it("shows Google disabled, with no caution note, while it is switched off", async () => {
    const { container } = form({ google: "disabled" });
    const google = screen.getByRole("button", { name: "Continue with Google" });
    expect(google).toBeDisabled();
    expect(google).not.toHaveAccessibleDescription();
    expect(screen.queryByText(/isn’t available yet/)).toBeNull();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("hides Google entirely when it is off, and the link option still follows 'or'", () => {
    form({ google: "off" });
    expect(screen.queryByRole("button", { name: "Continue with Google" })).toBeNull();
    expect(screen.getByText("or")).toBeInTheDocument();
    expect(linkButton()).toBeInTheDocument();
  });

  it("puts Google, then the sign-in link, after 'or', below the password sign-in", () => {
    form({ google: "on" });
    const order = [...document.querySelectorAll("button, a")]
      .map((b) => b.textContent)
      .filter((t) => t && /^(Sign in|Continue with Google|Email me a sign-in link)$/.test(t));
    expect(order).toEqual(["Sign in", "Continue with Google", "Email me a sign-in link"]);
  });

  it("shows why a link failed", () => {
    form({ initialError: "That sign-in link has expired. Request a new one below." });
    expect(screen.getByRole("alert")).toHaveTextContent("has expired");
  });
});

describe("SignInForm: Forgot password?", () => {
  const forgot = () => screen.queryByRole("link", { name: "Forgot password?" });

  it("links to the reset page under the password when signing in", () => {
    form({ passwordReset: true });
    expect(forgot()).toHaveAttribute("href", "/sign-in/forgot");
  });

  it("is never offered at registration", () => {
    form({ mode: "register", passwordReset: true });
    expect(forgot()).not.toBeInTheDocument();
  });

  it("is not shown while password reset is off (the default)", () => {
    form();
    expect(forgot()).not.toBeInTheDocument();
  });
});

describe("validate", () => {
  const ok = { name: "Ada", email: "ada@example.com", password: "x" };
  it("asks for a name and an email at registration, never a password", () => {
    expect(validate("register", { ...ok, password: "" })).toEqual({});
    expect(validate("register", { ...ok, name: "" })).toEqual({ name: "Enter your name." });
  });
  it("asks for a password, never a name, when signing in", () => {
    expect(validate("sign-in", { ...ok, name: "" })).toEqual({});
    expect(validate("sign-in", { ...ok, password: "" })).toEqual({
      password: "Enter your password.",
    });
  });
});

describe("registerError and codeError", () => {
  it("name the cause where they can", () => {
    expect(registerError({ status: 403, code: "EMAIL_NOT_ALLOWED" })).toMatch(
      /can’t send email to this address/,
    );
    expect(registerError({ status: 429, code: "CODE_COOLDOWN", message: "Wait 12 seconds" })).toBe(
      "Wait 12 seconds",
    );
    expect(registerError({ status: 429 })).toMatch(/Too many attempts/);
    expect(registerError({ status: 400, code: "RESEND_RECIPIENT_NOT_ALLOWED" })).toBe(
      "We couldn’t send the code. Try again in a moment.",
    );
    expect(registerError({ status: 502, code: "EMAIL_DELIVERY_FAILED" })).toMatch(/send the code/);
    expect(codeError({ status: 400, code: "OTP_EXPIRED" })).toMatch(/expired/);
    expect(codeError({ status: 403, code: "TOO_MANY_ATTEMPTS" })).toMatch(/Too many wrong codes/);
    expect(codeError({ status: 0 })).toMatch(/couldn’t reach the server/);
  });
});
