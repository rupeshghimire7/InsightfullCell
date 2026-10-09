/** RFC 9457 problem details as the API (and the BFF route) render them. */
export interface Problem {
  type?: string;
  title: string;
  status: number;
  detail?: string;
  code: string;
  hint?: string;
  request_id?: string;
  errors?: { field: string; message: string }[];
}

export class ProblemError extends Error {
  readonly problem: Problem;

  constructor(problem: Problem) {
    super(problem.detail ?? problem.title);
    this.name = "ProblemError";
    this.problem = problem;
  }
}

const GENERIC: Problem = {
  title: "Something went wrong",
  status: 500,
  code: "unknown",
  detail: "The request could not be completed.",
  hint: "Try again. If it keeps happening, contact support.",
};

function isProblem(value: unknown): value is Problem {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as Problem).title === "string" &&
    typeof (value as Problem).code === "string" &&
    typeof (value as Problem).status === "number"
  );
}

/** Read an error response without ever throwing: non-JSON bodies become a generic problem. */
export async function readProblem(response: Response): Promise<Problem> {
  try {
    const body: unknown = await response.json();
    if (isProblem(body)) return body;
  } catch {
    // fall through
  }
  return { ...GENERIC, status: response.status };
}

export function describeError(error: unknown): Problem {
  if (error instanceof ProblemError) return error.problem;
  if (error instanceof DOMException && error.name === "AbortError") {
    return { title: "Upload cancelled", status: 499, code: "cancelled" };
  }
  if (error instanceof TypeError) {
    return {
      title: "You appear to be offline",
      status: 0,
      code: "network",
      detail: "The connection dropped before the request finished.",
      hint: "Check your connection and try again.",
    };
  }
  return GENERIC;
}
