import { render, screen } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { axe } from "../axe";
import { LocalTime } from "@/components/ui/local-time";
import { formatDate } from "@/lib/format";

const ISO = "2026-10-10T00:27:00Z";

describe("LocalTime", () => {
  it("renders UTC on the server, so hydration never depends on the server's clock", () => {
    const html = renderToString(<LocalTime iso={ISO} />);
    expect(html).toContain('dateTime="2026-10-10T00:27:00Z"');
    expect(html).toContain("2026-10-10 00:27");
    expect(html).toContain("UTC");
  });

  it("shows the viewer's local time in the browser", async () => {
    const { container } = render(<LocalTime iso={ISO} />);
    expect(screen.getByText(formatDate(ISO))).toHaveAttribute("datetime", ISO);
    expect(await axe(container)).toHaveNoViolations();
  });
});
