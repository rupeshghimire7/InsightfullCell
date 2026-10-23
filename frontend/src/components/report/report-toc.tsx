"use client";

import { useEffect, useRef, useState } from "react";

/**
 * The report's "On this page" list. As the page scrolls, the answer being read is marked
 * (`aria-current`) and the list scrolls itself so that entry stays in view.
 */
export function ReportToc({ items }: { items: { id: string; label: string }[] }) {
  const [active, setActive] = useState<string | null>(null);
  const list = useRef<HTMLOListElement>(null);

  useEffect(() => {
    const sections = items
      .map((item) => document.getElementById(item.id))
      .filter((element): element is HTMLElement => element !== null);
    if (sections.length === 0) return;
    // The answer being read is the last one whose top has passed the upper third of the window.
    const update = () => {
      const line = window.innerHeight / 3;
      let current = sections[0]?.id ?? null;
      for (const section of sections) {
        if (section.getBoundingClientRect().top <= line) current = section.id;
        else break;
      }
      setActive(current);
    };
    update();
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    return () => {
      window.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
    };
  }, [items]);

  useEffect(() => {
    const container = list.current;
    const link = active ? container?.querySelector<HTMLElement>(`[data-toc="${active}"]`) : null;
    if (!container || !link) return;
    // Scroll the list only (scrollIntoView would also move the page).
    const top = link.offsetTop - container.offsetTop;
    if (
      top < container.scrollTop ||
      top + link.offsetHeight > container.scrollTop + container.clientHeight
    ) {
      container.scrollTo({ top: top - container.clientHeight / 2 + link.offsetHeight / 2 });
    }
  }, [active]);

  return (
    <ol ref={list} className="relative max-h-[70vh] space-y-1 overflow-y-auto pr-1 text-sm">
      {items.map((item) => (
        <li key={item.id}>
          <a
            href={`#${item.id}`}
            data-toc={item.id}
            aria-current={item.id === active ? "location" : undefined}
            className="block rounded-control px-2 py-1 text-muted hover:bg-surface hover:text-text aria-[current=location]:bg-accent/10 aria-[current=location]:font-medium aria-[current=location]:text-text"
          >
            {item.label}
          </a>
        </li>
      ))}
    </ol>
  );
}
