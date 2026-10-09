import { toNextJsHandler } from "better-auth/next-js";
import type { NextRequest } from "next/server";
import { getAuth } from "@/lib/auth";

export const dynamic = "force-dynamic";

// Built lazily so `next build` does not need secrets.
export function GET(request: NextRequest) {
  return toNextJsHandler(getAuth()).GET(request);
}

export function POST(request: NextRequest) {
  return toNextJsHandler(getAuth()).POST(request);
}
