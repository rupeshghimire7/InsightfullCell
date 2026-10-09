import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { axe } from "../axe";
import { ServiceUnavailable } from "@/components/ui/service-unavailable";

describe("ServiceUnavailable", () => {
  it("explains the outage with a heading, an alert and a retry link, and passes axe", async () => {
    const { container } = render(<ServiceUnavailable retryHref="/sign-in" />);
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Service unavailable");
    expect(screen.getByRole("alert")).toHaveTextContent("Sign-in is unavailable right now");
    expect(screen.getByRole("alert")).toHaveTextContent("Try again in a minute");
    expect(screen.getByRole("link", { name: "Try again" })).toHaveAttribute("href", "/sign-in");
    expect(await axe(container)).toHaveNoViolations();
  });

  it("names the fix for developers", () => {
    render(<ServiceUnavailable retryHref="/" developerHint />);
    expect(screen.getByRole("alert")).toHaveTextContent(/make up.*make migrate/);
  });
});
