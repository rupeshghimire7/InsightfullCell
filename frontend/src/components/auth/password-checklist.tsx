import { CheckCircle, Circle } from "@/components/ui/icons";
import { passwordProblems, PASSWORD_RULES } from "@/lib/password-policy";

/**
 * The password rules as a live checklist under a new-password field. Each item says whether it is
 * met in words too ("met" / "not yet"), so it never relies on the icon or colour alone. The field
 * is described by the list (`id`), and changes are not announced on every keystroke: the form's
 * own error says what is missing when it is submitted.
 */
export function PasswordChecklist({
  id,
  password,
  email,
}: {
  id: string;
  password: string;
  email?: string | null;
}) {
  const broken = new Set(passwordProblems(password, email));
  return (
    <ul id={id} aria-label="Password rules" className="mt-2 space-y-1 text-xs">
      {PASSWORD_RULES.filter((rule) => rule.id !== "no-email-name" || email).map((rule) => {
        const met = !broken.has(rule.id);
        const Icon = met ? CheckCircle : Circle;
        return (
          <li
            key={rule.id}
            data-met={met}
            className={`flex items-center gap-1.5 ${met ? "text-text" : "text-muted"}`}
          >
            <Icon
              aria-hidden="true"
              weight={met ? "fill" : "regular"}
              className={`size-3.5 shrink-0 ${met ? "text-accent" : ""}`}
            />
            <span>
              {rule.label}
              <span className="sr-only">{met ? ": met" : ": not yet"}</span>
            </span>
          </li>
        );
      })}
    </ul>
  );
}
