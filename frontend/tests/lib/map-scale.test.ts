import { describe, expect, it } from "vitest";
import { slug } from "@/lib/download";
import { legend, stepOf, thresholds } from "@/lib/report/map-scale";
import { regionMap } from "./report-fixtures";

describe("map colour steps", () => {
  it("splits the values shown into five equal-count steps, like the export", () => {
    const values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
    const bounds = thresholds(values);
    expect(bounds).toEqual([2, 4, 6, 8]);
    expect(values.map((v) => stepOf(v, bounds))).toEqual([0, 0, 1, 1, 2, 2, 3, 3, 4, 4]);
    expect(thresholds([])).toEqual([]);
    expect(thresholds([5])).toEqual([5, 5, 5, 5]);
  });

  it("labels only the steps that have regions", () => {
    expect(legend(regionMap())).toEqual([
      { step: 0, label: "35%" },
      { step: 2, label: "60%" },
    ]);
    expect(legend(regionMap({ regions: [] }))).toEqual([]);
  });

  it("names downloads safely", () => {
    expect(slug("Sales <by> Région / 2026")).toBe("sales-by-region-2026");
    expect(slug("!!!")).toBe("chart");
  });
});
