import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminBoard } from "@/components/admin/admin-board";
import { AdminDashboard } from "@/components/admin/admin-dashboard";
import { AdminGate } from "@/components/admin/admin-gate";
import { axe } from "../axe";
import { accounts, overview } from "../lib/admin-fixtures";

const unlockAdmin = vi.fn();
const adminPeriod = vi.fn();
vi.mock("@/app/adminboard/actions", () => ({
  unlockAdmin: (...a: unknown[]) => unlockAdmin(...a),
  adminPeriod: (...a: unknown[]) => adminPeriod(...a),
}));

const noop = () => undefined;
const dashboard = (props: Partial<React.ComponentProps<typeof AdminDashboard>> = {}) =>
  render(
    <AdminDashboard
      overview={overview()}
      accounts={accounts}
      onPeriod={noop}
      onLock={noop}
      {...props}
    />,
  );

beforeEach(() => {
  unlockAdmin.mockReset();
  adminPeriod.mockReset();
});

describe("AdminGate", () => {
  it("asks for the key in a dialog and focuses it", async () => {
    const { container } = render(<AdminGate onUnlock={async () => null} />);
    const dialog = screen.getByRole("dialog", {
      name: "Please enter admin key for dashboard access.",
    });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    const key = within(dialog).getByLabelText("Admin key");
    expect(key).toHaveFocus();
    expect(key).toHaveAttribute("type", "password");
    expect(key).toHaveAttribute("autocomplete", "off");
    expect(await axe(container)).toHaveNoViolations();
  });

  it("asks for a key before sending, shows the server's answer, explains a lost connection", async () => {
    const onUnlock = vi
      .fn()
      .mockResolvedValueOnce("That admin key isn’t right.")
      .mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const user = userEvent.setup();
    render(<AdminGate onUnlock={onUnlock} />);
    await user.click(screen.getByRole("button", { name: "Unlock dashboard" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Enter the admin key");
    expect(onUnlock).not.toHaveBeenCalled();
    await user.type(screen.getByLabelText("Admin key"), "nope{Enter}");
    expect(onUnlock).toHaveBeenCalledWith("nope");
    expect(await screen.findByRole("alert")).toHaveTextContent("isn’t right");
    expect(screen.getByLabelText("Admin key")).toHaveValue("");
    await user.type(screen.getByLabelText("Admin key"), "k{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("couldn’t reach the server");
  });
});

describe("AdminBoard", { timeout: 30_000 }, () => {
  const ok = (days = 30) => ({
    status: "ok",
    token: "page-token",
    days,
    overview: { ...overview(), days },
    accounts,
  });

  it("shows no figures until the key is right, then the dashboard", async () => {
    unlockAdmin.mockResolvedValueOnce({
      status: "refused",
      message: "That admin key isn’t right.",
    });
    unlockAdmin.mockResolvedValueOnce(ok());
    const user = userEvent.setup();
    render(<AdminBoard initialDays={30} />);
    expect(screen.queryByRole("region", { name: "Totals" })).toBeNull();
    await user.type(screen.getByLabelText("Admin key"), "wrong{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("isn’t right");
    expect(screen.queryByRole("region", { name: "Totals" })).toBeNull();
    await user.type(screen.getByLabelText("Admin key"), "the-key{Enter}");
    expect(unlockAdmin).toHaveBeenLastCalledWith("the-key", 30);
    expect(await screen.findByRole("region", { name: "Totals" })).toBeVisible();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("switches period with the page's token, and Lock asks for the key again", async () => {
    unlockAdmin.mockResolvedValue(ok());
    adminPeriod.mockResolvedValue(ok(7));
    const user = userEvent.setup();
    render(<AdminBoard initialDays={30} />);
    await user.type(screen.getByLabelText("Admin key"), "the-key{Enter}");
    await user.click(await screen.findByRole("button", { name: "7 days" }));
    expect(adminPeriod).toHaveBeenCalledWith("page-token", 7);
    expect(await screen.findByRole("button", { name: "7 days" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.click(screen.getByRole("button", { name: "Lock" }));
    expect(screen.getByRole("dialog")).toBeVisible();
    expect(screen.queryByRole("region", { name: "Totals" })).toBeNull();
  });

  it("asks for the key again once the page's token has expired", async () => {
    unlockAdmin.mockResolvedValue(ok());
    adminPeriod.mockResolvedValue({ status: "locked" });
    const user = userEvent.setup();
    render(<AdminBoard initialDays={30} />);
    await user.type(screen.getByLabelText("Admin key"), "the-key{Enter}");
    await user.click(await screen.findByRole("button", { name: "Refresh" }));
    expect(await screen.findByRole("dialog")).toBeVisible();
  });
});

describe("AdminDashboard", { timeout: 30_000 }, () => {
  it("shows totals, spend against the cap in AUD, and passes axe", async () => {
    const { container } = dashboard({ overview: overview(), accounts: accounts });
    const totals = screen.getByRole("region", { name: "Totals" });
    expect(within(totals).getByText("People").nextSibling).toHaveTextContent("1");
    expect(within(totals).getByText("Files submitted").nextSibling).toHaveTextContent("5");
    const meter = screen.getByRole("meter", { name: "Model spend against the cap" });
    // US$1.25 x 1.60 = A$2.00 of A$4.00
    expect(meter).toHaveAttribute(
      "aria-valuetext",
      expect.stringMatching(/2\.00 of .*4\.00: Within/),
    );
    expect(await axe(container)).toHaveNoViolations();
  });

  it("warns as the spend nears the cap", () => {
    const near = overview();
    near.spend.total_usd = 2.4; // A$3.84 of A$4.00
    dashboard({ overview: near, accounts: accounts });
    expect(screen.getByRole("meter", { name: "Model spend against the cap" })).toHaveAttribute(
      "aria-valuetext",
      expect.stringMatching(/Nearly at the cap/),
    );
  });

  it("tracks each provider against its own cap, or none", () => {
    const noOverall = overview();
    noOverall.config.spend_cap_aud = null;
    noOverall.config.spend_cap_usd = null;
    dashboard({ overview: noOverall, accounts: accounts });
    // US$2.50 x 1.60 = A$4.00 of OpenAI's A$4.50; Gemini has no cap, so no meter.
    expect(screen.getByRole("meter", { name: "OpenAI spend against its cap" })).toHaveAttribute(
      "aria-valuetext",
      expect.stringMatching(/4\.00 of .*4\.50: Nearly at the cap/),
    );
    expect(screen.queryByRole("meter", { name: /Gemini/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("meter", { name: "Model spend against the cap" })).toBeNull();
    expect(screen.getByText("No limit of its own", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("No overall limit", { exact: false })).toBeInTheDocument();
    const config = screen.getByText("Spend cap, all providers").nextSibling;
    expect(config).toHaveTextContent("No limit");
    expect(screen.getByText("OpenAI cap").nextSibling).toHaveTextContent("4.50");
    expect(screen.getByText("Gemini cap").nextSibling).toHaveTextContent("No limit");
  });

  it("lists people with their sign-in details and usage, deleted accounts too", () => {
    dashboard({ overview: overview(), accounts: accounts });
    const people = screen.getByRole("region", { name: "People and their usage" });
    const rows = within(people).getAllByRole("row");
    expect(rows).toHaveLength(3); // header, Ada, a deleted account
    expect(rows[1]).toHaveTextContent("Ada Lovelace");
    expect(rows[1]).toHaveTextContent("ada@example.com");
    expect(rows[1]).toHaveTextContent("Password, Google · verified");
    expect(rows[1]).toHaveTextContent("4 (3 done)");
    expect(rows[1]).toHaveTextContent("5 (4 ok, 1 failed)");
    expect(rows[1]).toHaveTextContent("40 (3 backup)");
    expect(rows[2]).toHaveTextContent("Deleted account");
  });

  it("shows spend by model and the backup, never a key", () => {
    dashboard({ overview: overview(), accounts: accounts });
    const models = screen.getByRole("region", { name: "Spend by model" });
    expect(models).toHaveTextContent("gpt-4o-mini");
    expect(screen.getByText("OpenAI: gpt-4o-mini")).toBeInTheDocument();
    expect(screen.getByText(/OPENAI_ADMIN_KEY/)).toBeInTheDocument();
  });

  it("compares with OpenAI's own figures when they are available", () => {
    render(
      <AdminDashboard
        overview={overview({
          provider_reported: {
            openai: {
              status: "ok",
              currency: "usd",
              total_cost: "0.0512",
              by_model: [
                {
                  model: "gpt-4o-mini-2024-07-18",
                  input_tokens: 10,
                  output_tokens: 2,
                  requests: 3,
                },
              ],
            },
          },
        })}
        accounts={accounts}
        onPeriod={noop}
        onLock={noop}
      />,
    );
    const check = screen.getByText("OpenAI’s own figures").parentElement as HTMLElement;
    expect(within(check).getByText("USD 0.0512")).toBeInTheDocument();
    expect(within(check).getByText("US$0.0500")).toBeInTheDocument();
  });

  it("says when OpenAI's report could not be read", () => {
    render(
      <AdminDashboard
        overview={overview({
          provider_reported: { openai: { status: "unavailable", http_status: 401 } },
        })}
        accounts={[]}
        onPeriod={noop}
        onLock={noop}
      />,
    );
    expect(screen.getByText(/couldn’t be read right now \(HTTP 401\)/)).toBeInTheDocument();
  });

  it("has words for an empty deployment", () => {
    const empty = overview();
    empty.spend.by_day = [];
    empty.spend.by_model = [];
    empty.users = [];
    empty.runs.by_day = [];
    empty.runs.failures = [];
    dashboard({ overview: empty, accounts: [] });
    expect(screen.getByText("No model calls in this period.")).toBeInTheDocument();
    expect(screen.getByText("Nobody has signed up yet.")).toBeInTheDocument();
    expect(screen.getByText("No failed analyses.")).toBeInTheDocument();
  });
});
