import Link from "next/link";
import type { ReactNode } from "react";
import { LockButton } from "@/components/admin/admin-gate";
import { LocalTime } from "@/components/ui/local-time";
import { PageHeader } from "@/components/ui/page-header";
import type { Account, Overview, UsageUser } from "@/lib/admin-data";
import { PERIODS } from "@/lib/admin-data";
import { formatBytes, formatCount } from "@/lib/format";
import { providerLabel } from "@/lib/plans/labels";

/** Everyone's usage, model spend against the cap, and how the stack is configured. */
export function AdminDashboard({
  overview,
  accounts,
}: {
  overview: Overview;
  accounts: Account[];
}) {
  const rate = Number(overview.config.usd_to_aud);
  const aud = (usd: number) => usd * rate;
  const spend = overview.spend;
  const capAud = Number(overview.config.spend_cap_aud);
  const spentAud = aud(Number(spend.total_usd));
  const heldAud = aud(Number(spend.held_usd));
  const people = mergePeople(accounts, overview.users);
  const files = overview.users.reduce((sum, u) => sum + u.uploads, 0);
  const filesDone = overview.users.reduce((sum, u) => sum + u.uploads_completed, 0);
  const runsStarted = overview.users.reduce((sum, u) => sum + u.runs_started, 0);
  const succeeded = overview.runs.by_status.succeeded ?? 0;
  // Relative to when the figures were taken, so the page renders the same everywhere.
  const since = Date.parse(overview.generated_at) - overview.days * 86_400_000;
  const newPeople = accounts.filter((a) => Date.parse(a.createdAt) >= since).length;

  return (
    <main id="main" className="mx-auto w-full max-w-[1400px] px-4 py-8 sm:px-8">
      <PageHeader
        title="Admin dashboard"
        description={
          <>
            Everyone’s usage of this deployment ({overview.config.environment}, label{" "}
            <code className="font-mono text-xs">{overview.config.label}</code>). Figures as of{" "}
            <LocalTime iso={overview.generated_at} />.
          </>
        }
        actions={
          <>
            <nav aria-label="Period" className="flex rounded-control border border-border">
              {PERIODS.map((days) => (
                <Link
                  key={days}
                  href={`/dashboard?days=${days}`}
                  aria-current={days === overview.days ? "page" : undefined}
                  className="inline-flex min-h-9 items-center px-3 text-sm first:rounded-l-control last:rounded-r-control hover:bg-bg aria-[current=page]:bg-accent aria-[current=page]:text-accent-contrast"
                >
                  {days} days
                </Link>
              ))}
            </nav>
            <Link
              href={`/dashboard?days=${overview.days}`}
              className="inline-flex min-h-9 items-center rounded-control border border-border bg-surface px-4 text-sm font-medium hover:bg-bg"
            >
              Refresh
            </Link>
            <LockButton />
          </>
        }
      />

      <section aria-label="Totals" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Tile label="People" value={formatCount(accounts.length)}>
          {formatCount(newPeople)} joined in the last {overview.days} days ·{" "}
          {formatCount(overview.organizations)} workspaces
        </Tile>
        <Tile label="Files submitted" value={formatCount(files)}>
          {formatCount(filesDone)} uploaded completely ·{" "}
          {formatBytes(overview.storage.stored_bytes)} kept
        </Tile>
        <Tile label="Analyses started" value={formatCount(runsStarted)}>
          {formatCount(succeeded)} succeeded · {formatCount(overview.runs.by_status.failed ?? 0)}{" "}
          failed
        </Tile>
        <SpendTile spentAud={spentAud} heldAud={heldAud} capAud={capAud}>
          {formatCount(spend.calls)} model calls · {formatCount(spend.backup_calls)} by the backup
        </SpendTile>
      </section>

      <Section title="Model spend per day" note={`Last ${overview.days} days, in AUD.`}>
        <BarList
          label="Model spend per day"
          rows={spend.by_day.map((d) => ({
            key: d.day,
            name: d.day,
            value: aud(d.cost_usd),
            text: money(aud(d.cost_usd)),
            detail: `${formatCount(d.calls)} calls${d.backup_calls ? `, ${formatCount(d.backup_calls)} backup` : ""} · ${formatCount(d.tokens)} tokens`,
          }))}
          empty="No model calls in this period."
        />
      </Section>

      <Section
        title="Spend by model"
        note="Our own count: every attempt, at list prices, rounded up. Failed attempts that may still have been billed are counted at their estimate."
      >
        <Table
          caption="Spend by model"
          head={[
            "Provider",
            "Model",
            "Calls",
            "Answered",
            "Backup",
            "Input tokens",
            "Output tokens",
            "Latency p50 / p95",
            "Cost (AUD)",
            "Cost (USD)",
          ]}
          numeric={[2, 3, 4, 5, 6, 7, 8, 9]}
          rows={spend.by_model.map((m) => [
            providerLabel(m.provider) ?? m.provider,
            <code key="m" className="font-mono text-xs">
              {m.model}
            </code>,
            formatCount(m.calls),
            formatCount(m.ok_calls),
            formatCount(m.backup_calls),
            formatCount(m.input_tokens),
            formatCount(m.output_tokens),
            latency(m.latency_p50_ms, m.latency_p95_ms),
            money(aud(m.cost_usd)),
            usd(m.cost_usd),
          ])}
          empty="No model calls yet."
        />
        <ProviderCheck overview={overview} />
      </Section>

      <div className="grid gap-6 xl:grid-cols-3">
        <Section title="Call outcomes">
          <Table
            caption="Call outcomes"
            head={["Provider", "Outcome", "Calls"]}
            numeric={[2]}
            rows={spend.by_outcome.map((o) => [
              providerLabel(o.provider) ?? o.provider,
              o.outcome,
              formatCount(o.calls),
            ])}
            empty="No calls yet."
          />
        </Section>
        <Section title="By purpose">
          <Table
            caption="Spend by purpose"
            head={["Purpose", "Calls", "Cost (AUD)"]}
            numeric={[1, 2]}
            rows={spend.by_purpose.map((p) => [
              p.purpose,
              formatCount(p.calls),
              money(aud(p.cost_usd)),
            ])}
            empty="No calls yet."
          />
        </Section>
        <Section title="By label" note="LLM_USAGE_LABEL: test runs and the released demo apart.">
          <Table
            caption="Spend by label"
            head={["Label", "Calls", "Cost (AUD)", "Last call"]}
            numeric={[1, 2]}
            rows={spend.by_label.map((l) => [
              <code key="l" className="font-mono text-xs">
                {l.label}
              </code>,
              formatCount(l.calls),
              money(aud(l.cost_usd)),
              <LocalTime key="t" iso={l.last_at} />,
            ])}
            empty="No calls yet."
          />
        </Section>
      </div>

      <Section
        title="People"
        note="Sign-in details from the web app; usage from the API. Counts of files and analyses include ones deleted since."
      >
        <Table
          caption="People and their usage"
          head={[
            "Person",
            "Sign-in",
            "Workspace",
            "Joined",
            "Last active",
            "Files",
            "Kept",
            "Datasets",
            "Analyses",
            "Model calls",
            "Tokens",
            "Cost (AUD)",
          ]}
          numeric={[5, 6, 7, 8, 9, 10, 11]}
          rows={people.map((p) => [
            <div key="p" className="min-w-[12rem]">
              <p className="font-medium">{p.account?.name ?? "Deleted account"}</p>
              <p className="text-xs text-muted">{p.account?.email ?? p.id}</p>
            </div>,
            p.account ? signIn(p.account) : "",
            p.account?.workspace ?? "",
            p.account ? <LocalTime key="j" iso={p.account.createdAt} /> : "",
            lastActive(p),
            p.usage
              ? `${formatCount(p.usage.uploads)} (${formatCount(p.usage.uploads_completed)} done)`
              : "0",
            p.usage ? formatBytes(p.usage.stored_bytes) : formatBytes(0),
            formatCount(p.usage?.datasets ?? 0),
            p.usage ? runsCell(p.usage) : "0",
            p.usage
              ? `${formatCount(p.usage.model_calls)}${p.usage.backup_calls ? ` (${formatCount(p.usage.backup_calls)} backup)` : ""}`
              : "0",
            formatCount(p.usage?.tokens ?? 0),
            money(aud(p.usage?.cost_usd ?? 0)),
          ])}
          empty="Nobody has signed up yet."
        />
      </Section>

      <div className="grid gap-6 xl:grid-cols-2">
        <Section title="Analyses per day" note={`Last ${overview.days} days.`}>
          <BarList
            label="Analyses per day"
            rows={overview.runs.by_day.map((d) => ({
              key: d.day,
              name: d.day,
              value: d.runs,
              text: formatCount(d.runs),
              detail: `${formatCount(d.succeeded)} succeeded · ${formatCount(d.failed)} failed`,
            }))}
            empty="No analyses in this period."
          />
        </Section>
        <Section title="Analyses">
          <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
            {Object.entries(overview.runs.by_status).map(([status, n]) => (
              <Stat key={status} label={status.replace("_", " ")} value={formatCount(n)} />
            ))}
            <Stat label="Typical time" value={seconds(overview.runs.duration_p50_seconds)} />
            <Stat label="Slowest 5%" value={seconds(overview.runs.duration_p95_seconds)} />
          </dl>
          <Table
            caption="Most common failures"
            head={["Failure", "Analyses"]}
            numeric={[1]}
            rows={overview.runs.failures.map((f) => [
              <code key="c" className="font-mono text-xs">
                {f.code}
              </code>,
              formatCount(f.runs),
            ])}
            empty="No failed analyses."
          />
        </Section>
      </div>

      <div className="grid gap-6 xl:grid-cols-2">
        <Section title="Storage and queue">
          <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
            <Stat label="Datasets" value={formatCount(overview.storage.datasets)} />
            <Stat label="Files kept" value={formatBytes(overview.storage.stored_bytes)} />
            <Stat label="Removals pending" value={formatCount(overview.storage.purges_pending)} />
            {Object.entries(overview.storage.uploads_by_status).map(([status, n]) => (
              <Stat key={status} label={`Uploads ${status}`} value={formatCount(n)} />
            ))}
            <Stat label="Jobs waiting" value={formatCount(overview.queue.todo ?? 0)} />
            <Stat label="Jobs running" value={formatCount(overview.queue.doing ?? 0)} />
          </dl>
        </Section>
        <Section title="Configuration" note="As the API sees it. Keys are never shown.">
          <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
            <Pair term="Main model">
              {overview.config.provider === "none"
                ? "Off: runs use the built-in rules"
                : `${providerLabel(overview.config.provider) ?? overview.config.provider}: ${Object.values(overview.config.models).join(", ")}`}
            </Pair>
            <Pair term="Backup model">
              {overview.config.fallback_provider === "none"
                ? "None"
                : `${providerLabel(overview.config.fallback_provider) ?? overview.config.fallback_provider}: ${overview.config.fallback_model}`}
            </Pair>
            <Pair term="Spend cap">
              {money(capAud)} (US${overview.config.spend_cap_usd} at {overview.config.usd_to_aud}{" "}
              AUD per USD)
            </Pair>
            <Pair term="Cap per person">
              {Number(overview.config.user_spend_cap_aud) > 0
                ? money(Number(overview.config.user_spend_cap_aud))
                : "Off"}
            </Pair>
            <Pair term="Calls per minute">
              {overview.config.requests_per_minute || "No spacing"}
            </Pair>
            <Pair term="Output limit per call">
              {formatCount(overview.config.max_output_tokens)} tokens
            </Pair>
            <Pair term="Analyses at once">{overview.config.worker_concurrency}</Pair>
          </dl>
        </Section>
      </div>
    </main>
  );
}

interface Person {
  id: string;
  account: Account | null;
  usage: UsageUser | null;
}

/** Accounts (newest first) with their usage; usage of deleted accounts at the end. */
function mergePeople(accounts: Account[], usage: UsageUser[]): Person[] {
  const byId = new Map(usage.map((u) => [u.user_id, u]));
  const people: Person[] = accounts.map((account) => ({
    id: account.id,
    account,
    usage: byId.get(account.id) ?? null,
  }));
  const known = new Set(accounts.map((a) => a.id));
  for (const u of usage) {
    if (!known.has(u.user_id)) people.push({ id: u.user_id, account: null, usage: u });
  }
  return people;
}

const PROVIDERS: Record<string, string> = {
  credential: "Password",
  google: "Google",
  "magic-link": "Email link",
};

function signIn(account: Account): string {
  const methods = account.providers.map((p) => PROVIDERS[p] ?? p);
  return [methods.join(", ") || "Email link", account.emailVerified ? "verified" : null]
    .filter(Boolean)
    .join(" · ");
}

function lastActive(person: Person): ReactNode {
  const times = [person.account?.lastActiveAt, person.usage?.last_at, person.usage?.last_run_at]
    .filter((t): t is string => Boolean(t))
    .sort();
  const last = times.at(-1);
  return last ? <LocalTime iso={last} /> : "Never";
}

function runsCell(u: UsageUser): string {
  const parts = [`${formatCount(u.runs_succeeded)} ok`];
  if (u.runs_failed) parts.push(`${formatCount(u.runs_failed)} failed`);
  if (u.runs_active) parts.push(`${formatCount(u.runs_active)} running`);
  return `${formatCount(u.runs_started)} (${parts.join(", ")})`;
}

const AUD = new Intl.NumberFormat("en-AU", {
  style: "currency",
  currency: "AUD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
});

function money(value: number): string {
  return AUD.format(value);
}

function usd(value: number): string {
  return `US$${value.toFixed(4)}`;
}

function latency(p50: number | null, p95: number | null): string {
  if (p50 === null) return "";
  const s = (ms: number) => `${(ms / 1000).toFixed(1)} s`;
  return `${s(p50)} / ${s(p95 ?? p50)}`;
}

function seconds(value: number | null): string {
  if (value === null) return "None yet";
  return value < 90 ? `${Math.round(value)} s` : `${(value / 60).toFixed(1)} min`;
}

function Section({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section className="mt-8 min-w-0 space-y-3">
      <div>
        <h2 className="text-base font-semibold">{title}</h2>
        {note ? <p className="text-sm text-muted">{note}</p> : null}
      </div>
      {children}
    </section>
  );
}

function Tile({ label, value, children }: { label: string; value: string; children: ReactNode }) {
  return (
    <div className="rounded-panel border border-border bg-surface p-4">
      <p className="text-sm text-muted">{label}</p>
      <p className="tabular mt-1 font-mono text-2xl font-semibold">{value}</p>
      <p className="mt-1 text-xs text-muted">{children}</p>
    </div>
  );
}

/** Spend against the cap: the share used, as a meter with its numbers beside it. */
function SpendTile({
  spentAud,
  heldAud,
  capAud,
  children,
}: {
  spentAud: number;
  heldAud: number;
  capAud: number;
  children: ReactNode;
}) {
  const share = capAud > 0 ? Math.min(1, spentAud / capAud) : 1;
  const tone = share >= 0.95 ? "bg-danger" : share >= 0.8 ? "bg-warning" : "bg-chart";
  const state =
    share >= 1
      ? "Cap reached: runs use the rules"
      : share >= 0.8
        ? "Nearly at the cap"
        : "Within the cap";
  return (
    <div className="rounded-panel border border-border bg-surface p-4">
      <p className="text-sm text-muted">Model spend</p>
      <p className="tabular mt-1 font-mono text-2xl font-semibold">
        {money(spentAud)} <span className="text-sm font-normal text-muted">of {money(capAud)}</span>
      </p>
      <div
        role="meter"
        aria-label="Model spend against the cap"
        aria-valuemin={0}
        aria-valuemax={capAud}
        aria-valuenow={Number(spentAud.toFixed(4))}
        aria-valuetext={`${money(spentAud)} of ${money(capAud)}: ${state}`}
        className="mt-2 h-2 overflow-hidden rounded-full bg-bg"
      >
        <div
          className={`h-full rounded-full ${tone}`}
          style={{ width: `${(share * 100).toFixed(1)}%` }}
        />
      </div>
      <p className="mt-1 text-xs text-muted">
        {state}
        {heldAud > 0 ? ` · ${money(heldAud)} reserved by calls in progress` : ""} · {children}
      </p>
    </div>
  );
}

interface Bar {
  key: string;
  name: string;
  value: number;
  text: string;
  detail: string;
}

/** One series as horizontal bars, each labelled with its value (so the list is also its table). */
function BarList({ label, rows, empty }: { label: string; rows: Bar[]; empty: string }) {
  if (rows.length === 0) return <p className="text-sm text-muted">{empty}</p>;
  const max = Math.max(...rows.map((r) => r.value), 0);
  return (
    <ul
      aria-label={label}
      className="space-y-1.5 rounded-panel border border-border bg-surface p-4"
    >
      {rows.map((row) => (
        <li
          key={row.key}
          title={`${row.name}: ${row.text} · ${row.detail}`}
          className="grid grid-cols-[6.5rem_minmax(0,1fr)_auto] items-center gap-3 text-sm"
        >
          <span className="tabular font-mono text-xs text-muted">{row.name}</span>
          <span className="h-3 rounded-r-[4px] bg-bg">
            <span
              className="block h-full rounded-r-[4px] bg-chart"
              style={{ width: `${max > 0 ? Math.max(1, (row.value / max) * 100) : 0}%` }}
            />
          </span>
          <span className="tabular text-right font-mono text-xs">
            {row.text} <span className="text-muted">· {row.detail}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

function Table({
  caption,
  head,
  rows,
  numeric = [],
  empty,
}: {
  caption: string;
  head: string[];
  rows: ReactNode[][];
  numeric?: number[];
  empty: string;
}) {
  if (rows.length === 0) return <p className="text-sm text-muted">{empty}</p>;
  const right = new Set(numeric);
  return (
    <div
      role="region"
      aria-label={caption}
      tabIndex={0}
      className="overflow-x-auto rounded-panel border border-border bg-surface"
    >
      <table className="w-full border-collapse text-left text-[13px] leading-[1.35]">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="border-b border-border text-muted">
            {head.map((h, i) => (
              <th
                key={h}
                scope="col"
                className={`px-4 py-2 font-medium whitespace-nowrap ${right.has(i) ? "text-right" : ""}`}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((cells, r) => (
            <tr key={r} className="border-b border-border last:border-b-0 hover:bg-bg/60">
              {cells.map((cell, i) => (
                <td
                  key={i}
                  className={`px-4 py-2.5 align-top ${right.has(i) ? "tabular text-right whitespace-nowrap" : ""}`}
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-panel border border-border bg-surface px-3 py-2">
      <dt className="text-xs text-muted capitalize">{label}</dt>
      <dd className="tabular font-mono text-base font-semibold">{value}</dd>
    </div>
  );
}

function Pair({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="flex flex-col border-b border-border py-1.5">
      <dt className="text-xs text-muted">{term}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/** Our count next to what the providers themselves report, where they can. */
function ProviderCheck({ overview }: { overview: Overview }) {
  const report = overview.provider_reported.openai;
  const ours = Number(overview.spend.period_by_provider.openai ?? 0);
  return (
    <div className="grid gap-3 text-sm md:grid-cols-2">
      <div className="rounded-panel border border-border bg-surface p-4">
        <p className="font-semibold">OpenAI’s own figures</p>
        {report.status === "ok" ? (
          <>
            <p className="mt-1">
              OpenAI reports{" "}
              <strong className="tabular font-mono">
                {report.currency?.toUpperCase()} {Number(report.total_cost ?? 0).toFixed(4)}
              </strong>{" "}
              for the last {overview.days} days; we counted{" "}
              <strong className="tabular font-mono">{usd(ours)}</strong> in the same days.
            </p>
            <ul className="mt-2 space-y-0.5 text-xs text-muted">
              {(report.by_model ?? []).map((m) => (
                <li key={m.model}>
                  <code className="font-mono">{m.model}</code>: {formatCount(m.requests)} requests,{" "}
                  {formatCount(m.input_tokens)} in, {formatCount(m.output_tokens)} out
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-muted">
              OpenAI updates these with a delay of minutes to hours.
            </p>
          </>
        ) : report.status === "not_configured" ? (
          <p className="mt-1 text-muted">
            Set <code className="font-mono text-xs">OPENAI_ADMIN_KEY</code> (an admin key with read
            access to usage) on the API to see OpenAI’s own cost next to ours.
          </p>
        ) : (
          <p className="mt-1 text-muted">
            OpenAI’s usage report couldn’t be read right now
            {report.http_status ? ` (HTTP ${report.http_status})` : ""}. Our own count is above.
          </p>
        )}
      </div>
      <div className="rounded-panel border border-border bg-surface p-4">
        <p className="font-semibold">Gemini</p>
        <p className="mt-1 text-muted">
          Google offers no usage report for AI Studio keys, so Gemini is counted by us only, at paid
          list prices. Calls on the free tier are not billed, so the real cost can be lower, never
          higher. Check the bill at aistudio.google.com.
        </p>
      </div>
    </div>
  );
}
