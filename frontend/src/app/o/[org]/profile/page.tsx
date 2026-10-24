import type { Metadata } from "next";
import { ChangePasswordForm } from "@/components/auth/change-password-form";
import { ProfileNameForm } from "@/components/auth/profile-name-form";
import { PageHeader } from "@/components/ui/page-header";
import { requireSession } from "@/lib/session";

export const metadata: Metadata = { title: "Profile" };

export default async function ProfilePage() {
  const session = await requireSession();
  return (
    <>
      <PageHeader title="Profile" />
      <div className="space-y-10">
        <section aria-labelledby="account-title" className="space-y-3">
          <h2 id="account-title" className="text-lg font-semibold">
            Account
          </h2>
          <ProfileNameForm name={session.name} email={session.email} />
        </section>
        <section aria-labelledby="password-title" className="space-y-3">
          <h2 id="password-title" className="text-lg font-semibold">
            Change password
          </h2>
          <ChangePasswordForm email={session.email} />
        </section>
      </div>
    </>
  );
}
