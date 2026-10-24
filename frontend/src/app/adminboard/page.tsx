import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { connection } from "next/server";
import { AdminBoard } from "@/components/admin/admin-board";
import { adminEnabled } from "@/lib/admin-session";
import { getEnv } from "@/lib/env";

export const metadata: Metadata = { title: "Admin dashboard" };

/**
 * `/adminboard`: everyone's usage and model spend, behind the admin key (`ADMIN_DASHBOARD_KEY`),
 * asked for on every visit (ADR-044). The page itself renders no figures: they arrive only after
 * the key is checked. Switched off (no key configured), it does not exist.
 */
export default async function AdminDashboardPage({
  searchParams,
}: {
  searchParams: Promise<{ days?: string }>;
}) {
  await connection();
  if (!adminEnabled(getEnv())) notFound();
  const days = Number((await searchParams).days);
  return <AdminBoard initialDays={Number.isFinite(days) ? days : 30} />;
}
