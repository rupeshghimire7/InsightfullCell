import type { EventTicket, RunEvent } from "@/lib/runs/types";
import { FINAL_EVENTS } from "@/lib/runs/reducer";

/**
 * A fetch-based Server-Sent Events reader (IC-008). `EventSource` cannot send an
 * `Authorization` header, and the stream is authorised by a run-scoped ticket in that header.
 *
 * Behaviour:
 * - reconnects after a dropped connection, sending `Last-Event-ID`, so nothing is missed;
 * - fetches a new ticket on 401 and before the current one expires;
 * - treats a connection that delivers nothing (not even a keep-alive) for `stallMs` as broken,
 *   which is how a proxy that buffers responses shows itself;
 * - gives up after `maxFailures` consecutive failures and reports `"fallback"`, so the caller can
 *   poll instead.
 */

export interface SseMessage {
  id?: string;
  event?: string;
  data: string;
}

/** Incremental parser for the `text/event-stream` format (WHATWG HTML, 9.2). */
export class SseParser {
  private buffer = "";
  private data: string[] = [];
  private event: string | undefined;
  private id: string | undefined;

  feed(chunk: string): SseMessage[] {
    this.buffer += chunk;
    const messages: SseMessage[] = [];
    let newline: number;
    while ((newline = this.buffer.search(/\r\n|\r|\n/)) >= 0) {
      const line = this.buffer.slice(0, newline);
      const width = this.buffer.startsWith("\r\n", newline) ? 2 : 1;
      this.buffer = this.buffer.slice(newline + width);
      if (line === "") {
        if (this.data.length > 0) {
          messages.push({ id: this.id, event: this.event, data: this.data.join("\n") });
        }
        this.data = [];
        this.event = undefined;
        continue;
      }
      if (line.startsWith(":")) continue; // comment (keep-alive)
      const colon = line.indexOf(":");
      const field = colon < 0 ? line : line.slice(0, colon);
      let value = colon < 0 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "data") this.data.push(value);
      else if (field === "event") this.event = value;
      else if (field === "id" && !value.includes("\0")) this.id = value;
    }
    return messages;
  }
}

export type StreamState = "connecting" | "live" | "reconnecting";
export type StreamResult = "ended" | "fallback" | "aborted";

export interface FollowOptions {
  /** Mints a ticket for this run (through the web app). Throws if it cannot. */
  getTicket: () => Promise<EventTicket>;
  onEvent: (event: RunEvent) => void;
  onState?: (state: StreamState) => void;
  /** Sequence number of the newest event already shown. */
  lastEventId?: number;
  signal: AbortSignal;
  fetchImpl?: typeof fetch;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
  maxFailures?: number;
  stallMs?: number;
}

const REFRESH_TICKET_BEFORE_MS = 30_000;

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

function isRunEvent(value: unknown): value is RunEvent {
  if (typeof value !== "object" || value === null) return false;
  const event = value as Partial<RunEvent>;
  return typeof event.seq === "number" && typeof event.type === "string";
}

export async function followRunEvents(options: FollowOptions): Promise<StreamResult> {
  const fetchImpl = options.fetchImpl ?? fetch.bind(globalThis);
  const sleep = options.sleep ?? defaultSleep;
  const now = options.now ?? Date.now;
  const maxFailures = options.maxFailures ?? 4;
  const stallMs = options.stallMs ?? 40_000;
  const { signal } = options;

  let lastId = options.lastEventId ?? 0;
  let ticket: EventTicket | null = null;
  let failures = 0;
  let everConnected = false;

  const fail = async (): Promise<boolean> => {
    failures += 1;
    if (failures >= maxFailures) return false;
    options.onState?.("reconnecting");
    await sleep(Math.min(8_000, 500 * 2 ** (failures - 1)));
    return true;
  };

  while (!signal.aborted) {
    options.onState?.(everConnected ? "reconnecting" : "connecting");
    const connection = new AbortController();
    const abortConnection = () => connection.abort();
    signal.addEventListener("abort", abortConnection, { once: true });
    let reader: ReadableStreamDefaultReader<string> | undefined;
    let stalled = false;
    let stallTimer: ReturnType<typeof setTimeout> | undefined;
    const armStall = () => {
      clearTimeout(stallTimer);
      stallTimer = setTimeout(() => {
        stalled = true;
        connection.abort();
        // Not every fetch propagates an abort into the body: cancel the read as well.
        reader?.cancel().catch(() => undefined);
      }, stallMs);
    };
    try {
      if (!ticket || Date.parse(ticket.expires_at) - now() < REFRESH_TICKET_BEFORE_MS) {
        ticket = await options.getTicket();
      }
      armStall();
      const response = await fetchImpl(ticket.url, {
        headers: {
          Accept: "text/event-stream",
          Authorization: `Bearer ${ticket.ticket}`,
          ...(lastId > 0 ? { "Last-Event-ID": String(lastId) } : {}),
        },
        cache: "no-store",
        signal: connection.signal,
      });
      if (response.status === 401) {
        ticket = null; // expired or rotated keys: mint a new one
        if (!(await fail())) return "fallback";
        continue;
      }
      const type = response.headers.get("content-type") ?? "";
      if (!response.ok || !response.body || !type.startsWith("text/event-stream")) {
        if (!(await fail())) return "fallback";
        continue;
      }

      everConnected = true;
      failures = 0;
      options.onState?.("live");
      const parser = new SseParser();
      reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      let finished = false;
      for (;;) {
        armStall();
        const { value, done } = await reader.read();
        if (done) break;
        for (const message of parser.feed(value)) {
          let parsed: unknown;
          try {
            parsed = JSON.parse(message.data);
          } catch {
            continue;
          }
          if (!isRunEvent(parsed) || parsed.seq <= lastId) continue;
          lastId = parsed.seq;
          options.onEvent(parsed);
          if (FINAL_EVENTS.has(parsed.type)) finished = true;
        }
      }
      if (finished) return "ended";
      if (stalled && !(await fail())) return "fallback";
      // Otherwise the server closed a stream that is not finished (its ticket ran out, a
      // deploy): resume where it stopped.
    } catch {
      if (signal.aborted) return "aborted";
      if (!(await fail())) return "fallback";
    } finally {
      clearTimeout(stallTimer);
      signal.removeEventListener("abort", abortConnection);
    }
  }
  return "aborted";
}
