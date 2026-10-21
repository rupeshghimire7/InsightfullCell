import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { axe } from "../axe";
import NotFound from "@/app/not-found";

const navigation = vi.hoisted(() => ({ pathname: "/o/acme/runs/missing" }));
vi.mock("next/navigation", () => ({ usePathname: () => navigation.pathname }));

describe("NotFound", () => {
  beforeEach(() => {
    navigation.pathname = "/o/acme/runs/missing";
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("names the problem, shows the address and links home, and passes axe", async () => {
    const { container } = render(<NotFound />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Page not found");
    expect(screen.getByText("Error 404")).toBeInTheDocument();
    expect(screen.getByText("Link you followed")).toBeInTheDocument();
    expect(screen.getByText("/o/acme/runs/missing")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to your workspace" })).toHaveAttribute("href", "/");
    expect(await axe(container)).toHaveNoViolations();
  });

  it("offers Go back only to visitors who came from inside the app", () => {
    vi.spyOn(document, "referrer", "get").mockReturnValue("https://elsewhere.example/page");
    const { unmount } = render(<NotFound />);
    expect(screen.queryByRole("button", { name: "Go back" })).not.toBeInTheDocument();
    unmount();

    vi.spyOn(document, "referrer", "get").mockReturnValue(`${window.location.origin}/o/acme`);
    const back = vi.spyOn(window.history, "back").mockImplementation(() => {});
    render(<NotFound />);
    screen.getByRole("button", { name: "Go back" }).click();
    expect(back).toHaveBeenCalledOnce();
  });

  it("leaves out the address on the root path", () => {
    navigation.pathname = "/";
    render(<NotFound />);
    expect(screen.queryByText("Link you followed")).not.toBeInTheDocument();
  });
});
