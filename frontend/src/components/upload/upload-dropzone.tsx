"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState, type DragEvent } from "react";
import { StartRunButton } from "@/components/runs/start-run-button";
import { Button } from "@/components/ui/button";
import { CheckCircle, FileArrowUp } from "@/components/ui/icons";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { describeError, type Problem } from "@/lib/problem";
import { formatBytes, formatPercent } from "@/lib/format";
import { browserUploadApi, type UploadResult } from "@/lib/upload/api";
import { ACCEPT_ATTRIBUTE, maxUploadBytes } from "@/lib/upload/constraints";
import { hashFile } from "@/lib/upload/hash";
import {
  ExpiredUrlError,
  PermanentPutError,
  putPart,
  TransientPutError,
} from "@/lib/upload/put-part";
import {
  uploadFile,
  UploadRejectedError,
  type UploaderDeps,
  type UploadProgress,
} from "@/lib/upload/uploader";

type State =
  | { kind: "idle" }
  | { kind: "uploading"; fileName: string; progress: UploadProgress }
  | { kind: "success"; fileName: string; result: UploadResult }
  | { kind: "error"; fileName: string; problem: Problem };

const DEFAULT_DEPS: UploaderDeps = { api: browserUploadApi, putPart, hashFile };

export interface UploadDropzoneProps {
  /** The organisation, so a finished upload can be analysed right away. */
  orgId?: string;
  /** Replaceable in tests. */
  deps?: UploaderDeps;
  /** Called after a successful upload (defaults to refreshing the server-rendered list). */
  onUploaded?: (result: UploadResult) => void;
}

/** Announce at most every 10% so screen readers are not flooded. */
function announcement(state: State): string {
  switch (state.kind) {
    case "uploading": {
      const { phase, uploadedBytes, totalBytes } = state.progress;
      if (phase === "verifying") return "Verifying the upload…";
      if (phase === "preparing") return "Preparing the upload…";
      const tenth = Math.floor((uploadedBytes / totalBytes) * 10) * 10;
      return `Uploading ${state.fileName}, ${tenth}% complete`;
    }
    case "success":
      return state.result.duplicate
        ? `${state.fileName} was already uploaded.`
        : `${state.fileName} uploaded.`;
    case "error":
      return `Upload failed: ${state.problem.title}`;
    default:
      return "";
  }
}

/** Storage failures keep their own (cell-value-free) explanation instead of a generic one. */
export function describeUploadError(error: unknown): Problem {
  if (error instanceof PermanentPutError) {
    return {
      title: "Storage refused the upload",
      status: 502,
      code: "storage-rejected",
      detail: error.message,
      hint: "Try again. If it keeps happening, contact support.",
    };
  }
  if (error instanceof TransientPutError || error instanceof ExpiredUrlError) {
    return {
      title: "The upload didn’t finish",
      status: 503,
      code: "storage-unavailable",
      detail: `${error.message} We retried several times.`,
      hint: "Check your connection, then choose the file again.",
    };
  }
  return describeError(error);
}

export function UploadDropzone({ orgId, deps = DEFAULT_DEPS, onUploaded }: UploadDropzoneProps) {
  const router = useRouter();
  const inputId = useId();
  const hintId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const [state, setState] = useState<State>({ kind: "idle" });
  const [dragging, setDragging] = useState(false);
  const busy = state.kind === "uploading";

  // Leaving mid-upload would silently abandon it.
  useEffect(() => {
    if (!busy) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [busy]);

  useEffect(() => () => controllerRef.current?.abort(), []);

  const start = useCallback(
    async (file: File) => {
      const controller = new AbortController();
      controllerRef.current = controller;
      setState({
        kind: "uploading",
        fileName: file.name,
        progress: {
          phase: "preparing",
          uploadedBytes: 0,
          totalBytes: file.size,
          partsDone: 0,
          partCount: 0,
        },
      });
      try {
        const result = await uploadFile(file, deps, {
          signal: controller.signal,
          onProgress: (progress) =>
            setState((current) =>
              current.kind === "uploading" ? { ...current, progress } : current,
            ),
        });
        setState({ kind: "success", fileName: file.name, result });
        if (onUploaded) onUploaded(result);
        else router.refresh();
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          setState({ kind: "idle" });
          return;
        }
        if (error instanceof UploadRejectedError) {
          setState({
            kind: "error",
            fileName: file.name,
            problem: {
              title: error.title,
              status: 422,
              code: "rejected",
              detail: error.message,
              hint: error.hint,
            },
          });
          return;
        }
        setState({ kind: "error", fileName: file.name, problem: describeUploadError(error) });
      } finally {
        if (inputRef.current) inputRef.current.value = ""; // allow picking the same file again
      }
    },
    [deps, onUploaded, router],
  );

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    if (busy) return;
    const file = event.dataTransfer.files[0];
    if (file) void start(file);
  };

  const limitMb = Math.round(maxUploadBytes() / (1024 * 1024));

  return (
    <section aria-labelledby={`${inputId}-title`} className="space-y-3">
      <div
        onDragOver={(event) => {
          event.preventDefault();
          if (!busy) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`rounded-panel border border-dashed bg-surface p-5 transition-colors duration-200 sm:p-6 ${
          dragging ? "border-accent bg-accent/5" : "border-border"
        }`}
      >
        <div className="flex items-start gap-4">
          <span
            aria-hidden="true"
            className={`hidden size-11 shrink-0 place-items-center rounded-full transition-colors sm:grid ${
              dragging ? "bg-accent text-accent-contrast" : "bg-accent/10 text-accent"
            }`}
          >
            <FileArrowUp className="size-5" />
          </span>
          <div className="min-w-0">
            <h2 id={`${inputId}-title`} className="text-base font-semibold">
              Upload a file
            </h2>
            <p id={hintId} className="mt-1 max-w-[60ch] text-muted">
              Drop a file here or choose one. CSV, TSV, Excel (.xlsx), Parquet or JSON, up to{" "}
              <span className="tabular">{limitMb}&nbsp;MB</span>.
            </p>
          </div>
        </div>

        <input
          ref={inputRef}
          id={inputId}
          type="file"
          accept={ACCEPT_ATTRIBUTE}
          className="sr-only"
          tabIndex={-1}
          aria-hidden="true"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void start(file);
          }}
        />

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button
            aria-describedby={hintId}
            loading={busy}
            onClick={() => inputRef.current?.click()}
          >
            {busy ? "Uploading…" : "Choose a file"}
          </Button>
          {busy ? (
            <Button variant="secondary" onClick={() => controllerRef.current?.abort()}>
              Cancel upload
            </Button>
          ) : null}
        </div>

        {state.kind === "uploading" ? <ProgressBar state={state} /> : null}
      </div>

      {state.kind === "success" ? <SuccessNote state={state} orgId={orgId} /> : null}
      {state.kind === "error" ? (
        <ProblemAlert
          title={state.problem.title}
          detail={state.problem.detail}
          hint={state.problem.hint}
          requestId={state.problem.request_id}
          action={
            <Button variant="secondary" onClick={() => inputRef.current?.click()}>
              Choose another file
            </Button>
          }
        />
      ) : null}

      <p className="sr-only" role="status" aria-live="polite" data-testid="upload-announcer">
        {announcement(state)}
      </p>
    </section>
  );
}

function ProgressBar({ state }: { state: Extract<State, { kind: "uploading" }> }) {
  const { phase, uploadedBytes, totalBytes, partsDone, partCount } = state.progress;
  const fraction = totalBytes > 0 ? uploadedBytes / totalBytes : 0;
  const label =
    phase === "verifying"
      ? "Verifying…"
      : phase === "preparing"
        ? "Preparing…"
        : `${formatBytes(uploadedBytes)} of ${formatBytes(totalBytes)}`;
  return (
    <div className="mt-4">
      <div className="flex items-baseline justify-between gap-4 text-[13px]">
        <span className="min-w-0 truncate" title={state.fileName}>
          {state.fileName}
        </span>
        <span className="tabular shrink-0 text-muted">{label}</span>
      </div>
      <div
        role="progressbar"
        aria-label={`Upload progress for ${state.fileName}`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(fraction * 100)}
        aria-valuetext={`${formatPercent(fraction)}, ${label}`}
        className="mt-2 h-2 overflow-hidden rounded-full bg-border"
      >
        <div
          className="h-full bg-accent transition-[width] duration-150 ease-out motion-reduce:transition-none"
          style={{ width: `${Math.round(fraction * 100)}%` }}
        />
      </div>
      {partCount > 1 ? (
        <p className="tabular mt-1 text-xs text-muted">
          {partsDone} of {partCount} parts
        </p>
      ) : null}
    </div>
  );
}

function SuccessNote({
  state,
  orgId,
}: {
  state: Extract<State, { kind: "success" }>;
  orgId?: string;
}) {
  const { result } = state;
  return (
    <div className="enter flex items-start gap-3 rounded-panel border border-border bg-surface p-4 text-sm">
      <CheckCircle
        aria-hidden="true"
        weight="fill"
        className="mt-0.5 size-5 shrink-0 text-success"
      />
      <div className="min-w-0 flex-1 space-y-3">
        <p>
          {result.duplicate ? (
            <>
              You already uploaded this exact file. It&rsquo;s saved as{" "}
              <strong className="font-semibold">{result.dataset.name}</strong>; nothing new was
              stored.
            </>
          ) : (
            <>
              Saved <strong className="font-semibold">{result.dataset.name}</strong>
              {result.dataset.version_count > 1
                ? ` as version ${result.dataset.version_count}`
                : ""}
              .
            </>
          )}
        </p>
        {orgId ? (
          <StartRunButton
            orgId={orgId}
            datasetId={result.dataset.id}
            versionId={result.version.id}
            label="Analyse this file"
          />
        ) : null}
      </div>
    </div>
  );
}
