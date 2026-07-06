import type { components } from "./schema";

type ValidationError = components["schemas"]["HTTPValidationError"];

export class ApiError extends Error {
  readonly status: number;
  readonly payload: unknown;
  readonly request?: string;

  constructor(status: number, payload: unknown, request?: string) {
    super(apiErrorMessage(payload, `Request failed (${status})`));
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
    this.request = request;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function validationMessage(error: ValidationError): string | null {
  if (!Array.isArray(error.detail) || error.detail.length === 0) return null;
  return error.detail.map((item) => item.msg).join("; ");
}

/** Wraps a client call, labeling failures with the request (e.g. "GET /api/watchlist")
 * so ErrorBlock can show context, and turning thrown network errors into an ApiError
 * with status 0 instead of an unhandled rejection. */
export async function req<T>(
  label: string,
  fn: () => Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  let r: Awaited<ReturnType<typeof fn>>;
  try {
    r = await fn();
  } catch (e) {
    throw new ApiError(0, { message: e instanceof Error ? e.message : String(e) }, label);
  }
  if (r.error !== undefined) throw new ApiError(r.response.status, r.error, label);
  if (r.data === undefined) throw new ApiError(r.response.status, { message: "Empty response." }, label);
  return r.data;
}

export function apiErrorMessage(error: unknown, fallback = "Something went wrong."): string {
  if (error instanceof Error && !(error instanceof ApiError)) return error.message;
  const payload = error instanceof ApiError ? error.payload : error;
  if (!isRecord(payload)) return fallback;

  const detail = payload.detail;
  if (isRecord(detail) && typeof detail.message === "string") return detail.message;
  if (typeof detail === "string") return detail;
  const validation = validationMessage(payload as ValidationError);
  if (validation) return validation;
  if (typeof payload.message === "string") return payload.message;
  return fallback;
}
