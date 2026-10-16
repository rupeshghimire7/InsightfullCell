import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { axe } from "../axe";
import { DeleteAction } from "@/components/ui/delete-action";
import type { RunsApi } from "@/lib/runs/api";

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));

describe("DeleteAction", () => {
  it("deletes a dataset after asking, then leaves its page", async () => {
    const user = userEvent.setup();
    const api = {
      removeDataset: vi.fn(async () => ({ run_ids: [], files_removed_after: "2026-10-23" })),
    } as unknown as RunsApi;
    const { container } = render(
      <DeleteAction
        kind="dataset"
        id="ds-1"
        redirectTo="/o/org-1"
        consequence="Every analysis of it goes with it."
        api={api}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Delete dataset…" }));
    expect(screen.getByRole("group", { name: "Confirm deleting this dataset" })).toHaveTextContent(
      "Delete this dataset? Every analysis of it goes with it. This cannot be undone.",
    );
    expect(await axe(container)).toHaveNoViolations();
    await user.click(screen.getByRole("button", { name: "Delete dataset" }));
    await waitFor(() => expect(api.removeDataset).toHaveBeenCalledWith("ds-1"));
    expect(router.replace).toHaveBeenCalledWith("/o/org-1");
    expect(screen.getByRole("status")).toHaveTextContent("The dataset was deleted.");
  });
});
