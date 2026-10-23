import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { axe } from "../axe";
import { AppShell } from "@/components/shell/app-shell";
import { Button } from "@/components/ui/button";
import { ProblemAlert } from "@/components/ui/problem-alert";

const replace = vi.fn();
const refresh = vi.fn();
const signOut = vi.fn(async () => ({ data: { success: true }, error: null }));
let pathname = "/o/org-1";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, refresh }),
  usePathname: () => pathname,
}));
vi.mock("@/lib/auth-client", () => ({ authClient: { signOut: () => signOut() } }));

describe("AppShell", () => {
  it("provides landmarks, a skip link and a labelled navigation", async () => {
    const { container } = render(
      <AppShell orgId="org-1" email="ada@example.com">
        <h1>Home</h1>
      </AppShell>,
    );
    expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute(
      "href",
      "#main",
    );
    expect(screen.getByRole("banner")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveAttribute("id", "main");
    expect(screen.getByRole("link", { name: "Home" })).toHaveAttribute("href", "/o/org-1");
    expect(screen.getByRole("link", { name: "Runs" })).toHaveAttribute("href", "/o/org-1/runs");
    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute("href", "/adminboard");
    expect(await axe(container)).toHaveNoViolations();
  });

  it.each([
    ["/o/org-1", "Home"],
    ["/o/org-1/datasets/d1", "Home"],
    ["/o/org-1/runs", "Runs"],
    ["/o/org-1/runs/r1", "Runs"],
  ])("marks only the current section on %s", (path, current) => {
    pathname = path;
    render(
      <AppShell orgId="org-1" email="ada@example.com">
        <p>content</p>
      </AppShell>,
    );
    for (const name of ["Home", "Runs"]) {
      const link = screen.getByRole("link", { name });
      if (name === current) expect(link).toHaveAttribute("aria-current", "page");
      else expect(link).not.toHaveAttribute("aria-current");
    }
    pathname = "/o/org-1";
  });

  it("signs out and returns to the sign-in page", async () => {
    const user = userEvent.setup();
    render(
      <AppShell orgId="org-1" email="ada@example.com">
        <p>content</p>
      </AppShell>,
    );
    // The header button (the menu holds a second one for narrow screens).
    await user.click(screen.getAllByRole("button", { name: "Sign out" })[0]!);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/sign-in"));
    expect(signOut).toHaveBeenCalledTimes(1);
    expect(refresh).toHaveBeenCalled();
  });
});

describe("Header menu and theme", () => {
  it("lists the about, contact, licence and profile pages", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <AppShell orgId="org-1" email="ada@example.com">
        <p>content</p>
      </AppShell>,
    );
    await user.click(screen.getByText("", { selector: "summary[aria-label='Menu']" }));
    const menu = screen.getByRole("navigation", { name: "More" });
    for (const [name, href] of [
      ["About us", "/o/org-1/about"],
      ["Contact", "/o/org-1/contact"],
      ["License", "/o/org-1/license"],
      ["Profile", "/o/org-1/profile"],
    ]) {
      expect(within(menu).getByRole("link", { name })).toHaveAttribute("href", href);
    }
    expect(await axe(container)).toHaveNoViolations();
  });

  it("switches the theme and remembers it in a cookie", async () => {
    const user = userEvent.setup();
    document.documentElement.dataset.theme = "light";
    render(
      <AppShell orgId="org-1" email="ada@example.com" theme="light">
        <p>content</p>
      </AppShell>,
    );
    await user.click(screen.getByRole("button", { name: "Switch to dark theme" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(document.cookie).toContain("ic_theme=dark");
    expect(screen.getByRole("button", { name: "Switch to light theme" })).toBeInTheDocument();
    delete document.documentElement.dataset.theme;
  });
});

describe("Button", () => {
  it("keeps its label, announces busy state and ignores clicks while loading", async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();
    render(
      <Button loading onClick={onClick}>
        Saving…
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Saving…" });
    expect(button).toHaveAttribute("aria-disabled", "true");
    await user.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("fires onClick when idle and defaults to type=button", async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Go</Button>);
    await userEvent.setup().click(screen.getByRole("button", { name: "Go" }));
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button")).toHaveAttribute("type", "button");
  });
});

describe("ProblemAlert", () => {
  it("is announced, explains the fix and shows a copyable reference", async () => {
    const { container } = render(
      <ProblemAlert
        title="Upload failed"
        detail="The connection dropped."
        hint="Try again."
        requestId="req-1"
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Upload failed");
    expect(screen.getByRole("alert")).toHaveTextContent("Try again.");
    expect(screen.getByText("req-1").tagName).toBe("CODE");
    expect(await axe(container)).toHaveNoViolations();
  });
});
