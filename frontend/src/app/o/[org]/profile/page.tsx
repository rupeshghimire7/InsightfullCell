import type { Metadata } from "next";
import { ChangePasswordForm } from "@/components/auth/change-password-form";
import { PageHeader } from "@/components/ui/page-header";
import { requireSession } from "@/lib/session";

export const metadata: Metadata = { title: "Profile" };

export default async function ProfilePage() {
  const session = await requireSession();
  return (
    <>
      <PageHeader title="Profile" description="Your account and your password." />
      <div className="space-y-10">
        <section aria-labelledby="account-title" className="space-y-3">
          <h2 id="account-title" className="text-lg font-semibold">
            Account
          </h2>
          <dl className="grid max-w-xl grid-cols-[auto_1fr] gap-x-6 gap-y-2 rounded-panel border border-border bg-surface p-5 text-sm">
            <dt className="text-muted">Name</dt>
            <dd className="min-w-0 break-words">{session.name || "Not set"}</dd>
            <dt className="text-muted">Email</dt>
            <dd className="min-w-0 break-words">{session.email}</dd>
          </dl>
        </section>
        <section aria-labelledby="password-title" className="space-y-3">
          <h2 id="password-title" className="text-lg font-semibold">
            Change password
          </h2>
          <ChangePasswordForm />
        </section>
      </div>
    </>
  );
}
