import Link from "next/link";

export default function NotFound() {
  return (
    <main id="main" className="mx-auto max-w-xl px-4 py-16">
      <h1 className="text-2xl font-semibold">Page not found</h1>
      <p className="mt-2 text-muted">The link may be old, or the page may have moved.</p>
      <p className="mt-4">
        <Link href="/" className="font-medium text-accent underline underline-offset-2">
          Go to your home page
        </Link>
      </p>
    </main>
  );
}
