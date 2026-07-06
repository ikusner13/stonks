import type { components } from "./schema";

type ValidationError = components["schemas"]["HTTPValidationError"];

export class ApiError extends Error {
  readonly status: number;
  readonly payload: unknown;

  constructor(status: number, payload: unknown) {
    super(apiErrorMessage(payload, `Request failed (${status})`));
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function validationMessage(error: ValidationError): string | null {
  if (!Array.isArray(error.detail) || error.detail.length === 0) return null;
  return error.detail.map((item) => item.msg).join("; ");
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
