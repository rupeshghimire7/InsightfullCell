import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "../axe";
import { describe, expect, it, vi } from "vitest";
import { describeUploadError, UploadDropzone } from "@/components/upload/upload-dropzone";
import { ExpiredUrlError, PermanentPutError, TransientPutError } from "@/lib/upload/put-part";
import { ProblemError } from "@/lib/problem";
import type { UploadResult } from "@/lib/upload/api";
import type { UploaderDeps } from "@/lib/upload/uploader";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

function result(overrides: Partial<UploadResult> = {}): UploadResult {
  return {
    duplicate: false,
    dataset: {
      id: "d1",
      name: "Home sales",
      created_at: "2030-01-01T00:00:00Z",
      version_count: 1,
      latest_version: null,
    },
    version: {
      id: "v1",
      dataset_id: "d1",
      sha256: "a".repeat(64),
      bytes: 10,
      format: "csv",
      created_at: "2030-01-01T00:00:00Z",
    },
    ...overrides,
  };
}

function depsFor(outcome: () => Promise<UploadResult>): UploaderDeps {
  return {
    api: {
      create: async ({ sizeBytes }) => ({
        upload_id: "u1",
        status: "pending",
        filename: "f.csv",
        format: "csv",
        size_bytes: sizeBytes,
        part_size_bytes: 5 * 1024 * 1024,
        part_count: 1,
        expires_at: "2030-01-01T00:00:00Z",
        parts: [{ part_number: 1, url: "https://s.test/1", content_length: sizeBytes }],
      }),
      presign: vi.fn(),
      complete: outcome,
      abort: vi.fn(async () => undefined),
    },
    putPart: async (_u, _b, onProgress) => {
      onProgress(5);
      return '"etag"';
    },
    hashFile: async () => "b".repeat(64),
    sleep: async () => undefined,
  };
}

const csv = () => new File(["id,name\n1,a\n"], "sales.csv", { type: "text/csv" });

describe("UploadDropzone", () => {
  it("has no detectable accessibility violations when idle", async () => {
    const { container } = render(<UploadDropzone deps={depsFor(async () => result())} />);
    expect(await axe(container)).toHaveNoViolations();
  });

  it("states accepted formats and the limit up front", () => {
    render(<UploadDropzone deps={depsFor(async () => result())} />);
    expect(screen.getByText(/CSV, TSV, Excel/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Choose a file" })).toBeEnabled();
  });

  it("uploads a chosen file and confirms what was saved", async () => {
    const onUploaded = vi.fn();
    const user = userEvent.setup();
    const { container } = render(
      <UploadDropzone deps={depsFor(async () => result())} onUploaded={onUploaded} />,
    );

    await user.upload(container.querySelector("input[type=file]") as HTMLInputElement, csv());

    expect(await screen.findByText(/Home sales/)).toBeInTheDocument();
    expect(screen.getByText(/Saved/)).toBeInTheDocument();
    expect(onUploaded).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("upload-announcer")).toHaveTextContent("sales.csv uploaded.");
  });

  it("tells the user when the exact file already exists", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <UploadDropzone
        deps={depsFor(async () => result({ duplicate: true }))}
        onUploaded={vi.fn()}
      />,
    );
    await user.upload(container.querySelector("input[type=file]") as HTMLInputElement, csv());
    expect(await screen.findByText(/already uploaded this exact file/)).toBeInTheDocument();
  });

  it("shows a specific, actionable message for an unsupported file", async () => {
    const user = userEvent.setup({ applyAccept: false });
    const { container } = render(<UploadDropzone deps={depsFor(async () => result())} />);
    await user.upload(
      container.querySelector("input[type=file]") as HTMLInputElement,
      new File(["x"], "notes.txt"),
    );
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("This file type isn’t supported");
    expect(alert).toHaveTextContent("Upload a CSV, TSV, Excel");
    expect(screen.getByRole("button", { name: "Choose another file" })).toBeInTheDocument();
  });

  it("renders API problems with their hint and reference", async () => {
    const user = userEvent.setup();
    const failing = depsFor(async () => {
      throw new ProblemError({
        title: "File content does not match its type",
        status: 422,
        code: "content-mismatch",
        detail: "This looks like a PDF, not a CSV.",
        hint: "Export the data as CSV and try again.",
        request_id: "req-123456",
      });
    });
    const { container } = render(<UploadDropzone deps={failing} />);
    await user.upload(container.querySelector("input[type=file]") as HTMLInputElement, csv());

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("This looks like a PDF, not a CSV.");
    expect(alert).toHaveTextContent("Export the data as CSV and try again.");
    expect(alert).toHaveTextContent("req-123456");
    expect(await axe(container)).toHaveNoViolations();
  });

  it("keeps the label while uploading and lets the user cancel", async () => {
    const user = userEvent.setup();
    let release: (() => void) | undefined;
    const deps = depsFor(async () => result());
    deps.putPart = (_u, _b, onProgress, signal) =>
      new Promise((resolve, reject) => {
        onProgress(2);
        signal?.addEventListener("abort", () =>
          reject(new DOMException("Cancelled", "AbortError")),
        );
        release = () => resolve('"e"');
      });
    const { container } = render(<UploadDropzone deps={deps} />);
    await user.upload(container.querySelector("input[type=file]") as HTMLInputElement, csv());

    expect(await screen.findByRole("progressbar", { name: /sales\.csv/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Uploading…" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(await axe(container)).toHaveNoViolations();

    await user.click(screen.getByRole("button", { name: "Cancel upload" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Choose a file" })).toBeInTheDocument(),
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(deps.api.abort).toHaveBeenCalledWith("u1");
    expect(release).toBeDefined();
  });
});

describe("describeUploadError", () => {
  it("keeps storage's own explanation instead of a generic message", () => {
    const rejected = describeUploadError(
      new PermanentPutError(
        "Storage did not return an ETag. Its CORS policy must expose the ETag header.",
      ),
    );
    expect(rejected.title).toBe("Storage refused the upload");
    expect(rejected.detail).toContain("ETag");

    const dropped = describeUploadError(
      new TransientPutError("The connection to storage dropped."),
    );
    expect(dropped.title).toBe("The upload didn’t finish");
    expect(dropped.detail).toContain("connection to storage dropped");
    expect(describeUploadError(new ExpiredUrlError("The upload link expired.")).code).toBe(
      "storage-unavailable",
    );
  });

  it("falls back to the generic description for anything else", () => {
    expect(describeUploadError(new Error("x")).title).toBe("Something went wrong");
  });
});
