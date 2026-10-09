"use client";

import { useEffect } from "react";
import { Button } from "@/components/ui/button";
import { ProblemAlert } from "@/components/ui/problem-alert";

export default function ErrorBoundary({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <main id="main" className="mx-auto max-w-xl px-4 py-16">
      <ProblemAlert
        title="Something went wrong on our side"
        detail="The page couldn’t be shown."
        hint="Try again. If it keeps happening, contact support with the reference."
        requestId={error.digest}
        action={<Button onClick={reset}>Try again</Button>}
      />
    </main>
  );
}
