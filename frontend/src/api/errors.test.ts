import { describe, expect, it } from "vitest";
import { ApiError, apiErrorMessage } from "./errors";

describe("apiErrorMessage", () => {
  it("extracts structured API messages", () => {
    expect(apiErrorMessage({ detail: { code: "budget_exceeded", message: "Daily budget hit." } })).toBe(
      "Daily budget hit.",
    );
  });

  it("extracts FastAPI validation messages", () => {
    expect(apiErrorMessage({ detail: [{ loc: ["body", "goal"], msg: "Field required", type: "missing" }] })).toBe(
      "Field required",
    );
  });

  it("unwraps ApiError payloads", () => {
    expect(apiErrorMessage(new ApiError(404, { detail: { message: "No market data." } }))).toBe(
      "No market data.",
    );
  });
});
