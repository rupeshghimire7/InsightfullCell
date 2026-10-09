import { describe, expect, it } from "vitest";
import { validateFile } from "@/lib/upload/constraints";

const MB = 1024 * 1024;

describe("validateFile", () => {
  it.each([
    "a.csv",
    "A.CSV",
    "x.tsv",
    "b.xlsx",
    "c.parquet",
    "d.json",
    "e.ndjson",
    "f.jsonl",
    "my.data.csv",
  ])("accepts %s", (name) => {
    expect(validateFile({ name, size: 10 }, 5 * MB)).toBeNull();
  });

  it("explains legacy .xls with a next step", () => {
    const result = validateFile({ name: "old.xls", size: 10 }, 5 * MB);
    expect(result?.hint).toMatch(/\.xlsx/);
  });

  it.each(["notes.txt", "archive.zip", "noextension", ".csv", "a.csv.exe"])(
    "rejects %s",
    (name) => {
      expect(validateFile({ name, size: 10 }, 5 * MB)?.title).toMatch(/isn’t supported/);
    },
  );

  it("rejects empty and oversized files with the limit in the message", () => {
    expect(validateFile({ name: "a.csv", size: 0 }, 5 * MB)?.title).toMatch(/empty/);
    const large = validateFile({ name: "a.csv", size: 6 * MB }, 5 * MB);
    expect(large?.detail).toContain("5 MB");
    expect(large?.hint).toMatch(/Parquet/);
  });

  it("allows a file exactly at the limit", () => {
    expect(validateFile({ name: "a.csv", size: 5 * MB }, 5 * MB)).toBeNull();
  });
});
