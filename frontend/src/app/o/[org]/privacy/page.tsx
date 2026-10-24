import type { Metadata } from "next";
import { DeleteAccountForm } from "@/components/auth/delete-account-form";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = { title: "Data & privacy" };

const PROMISES = [
  "We never sell or share your data, or use it to train AI models.",
  "Raw rows never reach an AI model.",
  "Only your workspace can see your datasets and reports.",
];

/** `/o/[org]/privacy`: what happens to people's data, and "Delete my account" (ADR-043). */
export default function PrivacyPage() {
  return (
    <>
      <PageHeader title="Data & privacy" />
      <div className="max-w-[70ch] space-y-10">
        <ul className="list-disc space-y-2 pl-5">
          {PROMISES.map((promise) => (
            <li key={promise}>{promise}</li>
          ))}
        </ul>
        <section aria-labelledby="delete-title" className="space-y-3">
          <h2 id="delete-title" className="text-lg font-semibold">
            Delete my account
          </h2>
          <p>Removes your account, datasets, analyses and reports. This can’t be undone.</p>
          <DeleteAccountForm />
        </section>
      </div>
    </>
  );
}
