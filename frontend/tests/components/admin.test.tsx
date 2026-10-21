import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminDashboard } from "@/components/admin/admin-dashboard";
import { AdminGate, LockButton } from "@/components/admin/admin-gate";
import { axe } from "../axe";
import { accounts, overview } from "../lib/admin-fixtures";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));
const fetchMock = vi.fn();

beforeEach(() => {
  refresh.mockReset();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("AdminGate", () => {
  it("asks for the key in a dialog and focuses it", async () => {
    const { container } = render(<AdminGate />);
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

  it("sends the key to this app's server only and reloads on success", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    const user = userEvent.setup();
    render(<AdminGate />);
    await user.type(screen.getByLabelText("Admin key"), "the-key{Enter}");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/session",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ key: "the-key" }) }),
    );
    expect(refresh).toHaveBeenCalled();
    expect(screen.getByLabelText("Admin key")).toHaveValue("");
  });

  it("says when the key is wrong, and when attempts are paused", async () => {
    const user = userEvent.setup();
    render(<AdminGate />);
    fetchMock.mockResolvedValueOnce(new Response("{}", { status: 401 }));
    await user.type(screen.getByLabelText("Admin key"), "nope{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("isn’t right");
    expect(screen.getByLabelText("Admin key")).toHaveAttribute("aria-invalid", "true");
    fetchMock.mockResolvedValueOnce(
      Response.json({ detail: "Too many wrong keys. Try again in 15 minutes." }, { status: 429 }),
    );
    await user.type(screen.getByLabelText("Admin key"), "again{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Try again in 15 minutes");
    expect(refresh).not.toHaveBeenCalled();
  });

  it("asks for a key before sending, and explains a lost connection", async () => {
    const user = userEvent.setup();
    render(<AdminGate />);
    await user.click(screen.getByRole("button", { name: "Unlock dashboard" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Enter the admin key");
    expect(fetchMock).not.toHaveBeenCalled();
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    await user.type(screen.getByLabelText("Admin key"), "k{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("couldn’t reach the server");
  });

  it("locks again", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    const user = userEvent.setup();
    render(<LockButton />);
    await user.click(screen.getByRole("button", { name: "Lock" }));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/session",
      expect.objectContaining({ method: "DELETE" }),
    );
    expect(refresh).toHaveBeenCalled();
  });
});

describe("AdminDashboard", { timeout: 30_000 }, () => {
  it("shows totals, spend against the cap in AUD, and passes axe", async () => {
    const { container } = render(<AdminDashboard overview={overview()} accounts={accounts} />);
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
    render(<AdminDashboard overview={near} accounts={accounts} />);
    expect(screen.getByRole("meter")).toHaveAttribute(
      "aria-valuetext",
      expect.stringMatching(/Nearly at the cap/),
    );
  });

  it("lists people with their sign-in details and usage, deleted accounts too", () => {
    render(<AdminDashboard overview={overview()} accounts={accounts} />);
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
    render(<AdminDashboard overview={overview()} accounts={accounts} />);
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
    render(<AdminDashboard overview={empty} accounts={[]} />);
    expect(screen.getByText("No model calls in this period.")).toBeInTheDocument();
    expect(screen.getByText("Nobody has signed up yet.")).toBeInTheDocument();
    expect(screen.getByText("No failed analyses.")).toBeInTheDocument();
  });
});
