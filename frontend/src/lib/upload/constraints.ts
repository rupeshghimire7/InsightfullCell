/** Client-side pre-checks. The API enforces the same rules; these only save a round trip. */

export const SUPPORTED_EXTENSIONS = [
  "csv",
  "tsv",
  "xlsx",
  "parquet",
  "json",
  "ndjson",
  "jsonl",
] as const;

export const ACCEPT_ATTRIBUTE = SUPPORTED_EXTENSIONS.map((ext) => `.${ext}`).join(",");

export const DEFAULT_MAX_UPLOAD_MB = 250;

export function maxUploadBytes(): number {
  const configured = Number(process.env.NEXT_PUBLIC_MAX_UPLOAD_MB);
  const mb = Number.isFinite(configured) && configured > 0 ? configured : DEFAULT_MAX_UPLOAD_MB;
  return mb * 1024 * 1024;
}

export interface Rejection {
  title: string;
  detail: string;
  hint: string;
}

export function validateFile(
  file: { name: string; size: number },
  maxBytes: number = maxUploadBytes(),
): Rejection | null {
  const dot = file.name.lastIndexOf(".");
  const extension = dot > 0 ? file.name.slice(dot + 1).toLowerCase() : "";

  if (extension === "xls") {
    return {
      title: "Legacy Excel files aren\u2019t supported",
      detail: `${file.name} is an .xls workbook.`,
      hint: "Save it as .xlsx or .csv and upload that.",
    };
  }
  if (!(SUPPORTED_EXTENSIONS as readonly string[]).includes(extension)) {
    return {
      title: "This file type isn\u2019t supported",
      detail: extension
        ? `.${extension} files can\u2019t be analysed.`
        : "The file has no extension.",
      hint: "Upload a CSV, TSV, Excel (.xlsx), Parquet or JSON file.",
    };
  }
  if (file.size === 0) {
    return {
      title: "The file is empty",
      detail: `${file.name} has no content.`,
      hint: "Choose a file that contains data.",
    };
  }
  if (file.size > maxBytes) {
    const limit = Math.round(maxBytes / (1024 * 1024));
    return {
      title: "The file is too large",
      detail: `Files can be up to ${limit} MB.`,
      hint: "Try Parquet for large data, or upload an aggregated extract.",
    };
  }
  return null;
}
