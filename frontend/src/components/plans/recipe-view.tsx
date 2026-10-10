import { DownloadSimple } from "@/components/ui/icons";
import { PrivacyNote } from "@/components/plans/privacy-note";
import { plansApi } from "@/lib/plans/api";
import type { Plan } from "@/lib/plans/types";

/**
 * The run's recipe (the approved plan as a Python script) and exactly what the planning model
 * was shown. Both are for checking: the script reproduces the cleaned table and charts from
 * the original file; the description shows that no rows or personal values were shared.
 */
export function RecipeView({ plan, source }: { plan: Plan; source: string | null }) {
  const runId = plan.run.id;
  return (
    <div className="space-y-12">
      <section aria-labelledby="recipe-title" className="space-y-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div className="space-y-1">
            <h2 id="recipe-title" className="text-lg font-semibold">
              The recipe
            </h2>
            <p className="max-w-[65ch] text-sm text-muted">
              Every step as a Python script: it loads your original file, applies the cleaning you
              approved, adds the planned features and draws the charts. Nothing in it was written by
              a language model.
            </p>
          </div>
          <a
            href={plansApi.recipeUrl(runId)}
            download
            className="press inline-flex min-h-9 shrink-0 items-center justify-center gap-2 rounded-control border border-transparent bg-accent px-4 text-sm font-medium text-accent-contrast hover:brightness-110 pointer-coarse:min-h-11"
          >
            <DownloadSimple aria-hidden="true" className="size-4" />
            Download recipe
          </a>
        </div>
        {source ? (
          <>
            <pre
              role="region"
              aria-label="The recipe's Python code"
              tabIndex={0}
              className="max-h-[32rem] overflow-auto rounded-panel border border-border bg-surface px-4 py-3 font-mono text-xs leading-relaxed"
            >
              <code>{source}</code>
            </pre>
            <p className="text-xs text-muted">
              Run it with <code className="font-mono">pip install duckdb</code>, then{" "}
              <code className="font-mono">python recipe.py YOUR_FILE</code>.
            </p>
          </>
        ) : (
          <p className="text-sm text-muted">
            The recipe is written once the cleaning plan exists. Reload this page in a moment.
          </p>
        )}
      </section>

      <section aria-labelledby="model-input-title" className="space-y-3">
        <h2 id="model-input-title" className="text-lg font-semibold">
          What the planning model was shown
        </h2>
        <p className="max-w-[65ch] text-sm text-muted">
          A description of your table, never its rows. Personal-data columns are reduced to their
          kind, numbers to rounded ranges without the lowest or highest value, and a category label
          appears only when several rows share it.
        </p>
        {plan.model_input ? (
          <details className="group rounded-panel border border-border bg-surface">
            <summary className="flex min-h-11 cursor-pointer items-center gap-2 px-4 text-sm font-medium select-none">
              Show the description exactly as it was sent
            </summary>
            <pre
              role="region"
              aria-label="The description sent to the model"
              tabIndex={0}
              className="max-h-[28rem] overflow-auto border-t border-border px-4 py-3 font-mono text-xs leading-relaxed"
            >
              {JSON.stringify(plan.model_input, null, 2)}
            </pre>
          </details>
        ) : (
          <p className="text-sm text-muted">This plan was made without a description.</p>
        )}
        {plan.rejected.length > 0 ? (
          <details className="rounded-panel border border-border bg-surface">
            <summary className="flex min-h-11 cursor-pointer items-center gap-2 px-4 text-sm font-medium select-none">
              {plan.rejected.length === 1
                ? "1 suggestion from the model was not used"
                : `${plan.rejected.length} suggestions from the model were not used`}
            </summary>
            <ul className="space-y-1.5 border-t border-border px-4 py-3 text-sm">
              {plan.rejected.map((r, i) => (
                <li key={i}>
                  <span className="font-medium">{r.item}</span>
                  <span className="text-muted">: {r.reason}</span>
                </li>
              ))}
            </ul>
          </details>
        ) : null}
      </section>

      <PrivacyNote />
    </div>
  );
}
