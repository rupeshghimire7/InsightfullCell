import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { axe } from "../axe";
import { AppShell } from "@/components/shell/app-shell";
import { Button } from "@/components/ui/button";
import { ProblemAlert } from "@/components/ui/problem-alert";

const replace = vi.fn();
const refresh = vi.fn();
const signOut = vi.fn(async () => ({ data: { success: true }, error: null }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace, refresh }) }));
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
    expect(await axe(container)).toHaveNoViolations();
  });

  it("signs out and returns to the sign-in page", async () => {
    const user = userEvent.setup();
    render(
      <AppShell orgId="org-1" email="ada@example.com">
        <p>content</p>
      </AppShell>,
    );
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/sign-in"));
    expect(signOut).toHaveBeenCalledTimes(1);
    expect(refresh).toHaveBeenCalled();
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
