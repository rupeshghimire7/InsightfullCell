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
  render(<SignInForm google="on" linkDelivery="email" {...props} />);

const linkButton = () => screen.getByRole("link", { name: "Email me a sign-in link" });
const offButton = () => screen.getByRole("button", { name: "Email me a sign-in link" });

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
  it("is accessible, asks for a name and a new password, and offers the link option", async () => {
    const { container } = form({ mode: "register" });
    expect(screen.getByLabelText("Name")).toHaveAttribute("autocomplete", "name");
    const password = screen.getByLabelText("Password");
    expect(password).toHaveAttribute("autocomplete", "new-password");
    expect(password).toHaveAccessibleDescription("At least 8 characters.");
    expect(screen.getByLabelText("Confirm password")).toHaveAttribute(
      "autocomplete",
      "new-password",
    );
    expect(linkButton()).toHaveAttribute("href", "/sign-in/email?mode=register");
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
    expect(screen.getByLabelText("Confirm password")).toHaveAccessibleDescription(
      "Type your password again.",
    );
    expect(signUpEmail).not.toHaveBeenCalled();
  });

  it("refuses passwords that don't match and focuses the confirmation", async () => {
    const user = userEvent.setup();
    form({ mode: "register" });
    await user.type(screen.getByLabelText("Name"), "Ada");
    await user.type(screen.getByLabelText("Email address"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "analytical engine");
    await user.type(screen.getByLabelText("Confirm password"), "analytical engines");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    const confirm = screen.getByLabelText("Confirm password");
    expect(confirm).toHaveFocus();
    expect(confirm).toHaveAccessibleDescription("The passwords don’t match.");
    expect(signUpEmail).not.toHaveBeenCalled();
  });

  it("shows and hides each password with its eye button", async () => {
    const user = userEvent.setup();
    form({ mode: "register" });
    const password = screen.getByLabelText("Password");
    const [showPassword, showConfirm] = screen.getAllByRole("button", { name: "Show password" });
    expect(password).toHaveAttribute("type", "password");
    await user.click(showPassword!);
    expect(password).toHaveAttribute("type", "text");
    expect(showPassword).toHaveAccessibleName("Hide password");
    expect(screen.getByLabelText("Confirm password")).toHaveAttribute("type", "password");
    await user.click(showPassword!);
    expect(password).toHaveAttribute("type", "password");
    expect(showConfirm).toHaveAttribute("type", "button");
  });

  it("creates the account and enters the app", async () => {
    const user = userEvent.setup();
    form({ mode: "register" });
    await user.type(screen.getByLabelText("Name"), " Ada Lovelace ");
    await user.type(screen.getByLabelText("Email address"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "analytical engine");
    await user.type(screen.getByLabelText("Confirm password"), "analytical engine");
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
    await user.type(screen.getByLabelText("Password"), "analytical engine");
    await user.type(screen.getByLabelText("Confirm password"), "analytical engine{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("already exists");
  });
});

describe("SignInForm: sign-in link", () => {
  it("opens the page that asks for the address, without sending anything", async () => {
    form();
    expect(linkButton()).toHaveAttribute("href", "/sign-in/email");
    expect(magicLink).not.toHaveBeenCalled();
  });

  it("labels the test sender as testing only and says who it works for", async () => {
    const user = userEvent.setup();
    const { container } = form({ linkDelivery: "resend-test" });
    expect(linkButton()).toHaveAccessibleDescription(/Testing only.*project owner’s email/);
    await user.hover(linkButton());
    expect(screen.getByRole("tooltip")).toBeVisible();
    expect(await axe(container)).toHaveNoViolations();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("has no testing note when real email is configured", () => {
    form({ linkDelivery: "email" });
    expect(linkButton()).not.toHaveAccessibleDescription();
  });

  it("opens the note only on hover while links go to the server log", async () => {
    const user = userEvent.setup();
    form({ linkDelivery: "log" });
    act(() => linkButton().focus());
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    await user.hover(linkButton());
    expect(screen.getByRole("tooltip")).toHaveTextContent("No email is sent yet");
    await user.unhover(linkButton());
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });
});

describe("SignInForm: sign-in links switched off", () => {
  it("keeps the button visible but inert, and says why only on hover", async () => {
    const user = userEvent.setup();
    const { container } = form({ linkDelivery: "off" });
    expect(offButton()).toHaveAttribute("aria-disabled", "true");
    expect(offButton()).not.toBeDisabled(); // still focusable, so the reason can be read
    expect(screen.queryByRole("link", { name: "Email me a sign-in link" })).toBeNull();
    await user.click(offButton());
    expect(magicLink).not.toHaveBeenCalled();
    await user.hover(offButton());
    expect(screen.getByRole("tooltip")).toHaveTextContent("Not available yet");
    expect(screen.getByRole("tooltip")).toHaveTextContent("password or Google");
    expect(await axe(container)).toHaveNoViolations();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    await user.unhover(offButton());
    act(() => offButton().focus()); // focus alone never opens it
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    expect(offButton()).toHaveAccessibleDescription(/Not available yet/);
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
    for (const mode of ["sign-in", "register"] as const) {
      form({ google: "off", mode });
      expect(screen.queryByRole("button", { name: "Continue with Google" })).toBeNull();
      expect(screen.getByText("or")).toBeInTheDocument();
      expect(linkButton()).toBeInTheDocument();
      cleanup();
    }
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

describe("validate", () => {
  const ok = {
    name: "Ada",
    email: "ada@example.com",
    password: "12345678",
    confirmPassword: "12345678",
  };
  it("accepts a password of exactly the minimum length at registration", () => {
    expect(validate("register", "password", ok)).toEqual({});
    expect(validate("register", "password", { ...ok, password: "1234567" })).toHaveProperty(
      "password",
    );
  });
  it("never asks for a name or a confirmation when signing in", () => {
    expect(validate("sign-in", "password", { ...ok, name: "", confirmPassword: "" })).toEqual({});
  });
  it("asks for the password again at registration, and the same one", () => {
    expect(validate("register", "password", { ...ok, confirmPassword: "" })).toEqual({
      confirmPassword: "Type your password again.",
    });
    expect(validate("register", "password", { ...ok, confirmPassword: "12345679" })).toEqual({
      confirmPassword: "The passwords don’t match.",
    });
  });
});
