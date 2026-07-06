import { describe, expect, it } from "vitest";
import { fmtCap, fmtIndicatorValue, fmtNum, fmtUsd, fmtUsd0, pct } from "./format";

describe("fmtNum", () => {
  it("formats null/undefined as n/a", () => {
    expect(fmtNum(null)).toBe("n/a");
    expect(fmtNum(undefined)).toBe("n/a");
  });

  it("formats integers with thousands separators, 0dp", () => {
    expect(fmtNum(1234)).toBe("1,234");
    expect(fmtNum(0)).toBe("0");
    expect(fmtNum(-1234)).toBe("-1,234");
  });

  it("formats non-integers to 2dp, stripping trailing zeros/dot", () => {
    expect(fmtNum(1234.5)).toBe("1,234.5");
    expect(fmtNum(1234.1)).toBe("1,234.1");
    expect(fmtNum(1234.25)).toBe("1,234.25");
    // rounds to an integer at 2dp -> the trailing ".00" is stripped entirely
    expect(fmtNum(1234.001)).toBe("1,234");
  });
});

describe("fmtCap", () => {
  it("formats null as n/a", () => {
    expect(fmtCap(null)).toBe("n/a");
  });

  it("scales to T/B/M and falls back to plain dollars", () => {
    expect(fmtCap(1.5e12)).toBe("$1.50T");
    expect(fmtCap(2.3e9)).toBe("$2.30B");
    expect(fmtCap(4.5e6)).toBe("$4.50M");
    expect(fmtCap(1234)).toBe("$1,234");
  });
});

describe("fmtUsd", () => {
  it("formats null/undefined as n/a", () => {
    expect(fmtUsd(null)).toBe("n/a");
    expect(fmtUsd(undefined)).toBe("n/a");
  });

  it("formats with 2dp and comma grouping", () => {
    expect(fmtUsd(10534.55)).toBe("$10,534.55");
    expect(fmtUsd(-1234.5)).toBe("-$1,234.50");
    expect(fmtUsd(0)).toBe("$0.00");
  });
});

describe("fmtUsd0", () => {
  it("formats null/undefined as n/a", () => {
    expect(fmtUsd0(null)).toBe("n/a");
  });

  it("formats whole dollars with comma grouping", () => {
    expect(fmtUsd0(10534.55)).toBe("$10,535");
    expect(fmtUsd0(-1234.5)).toBe("-$1,235");
    expect(fmtUsd0(0)).toBe("$0");
  });
});

describe("pct", () => {
  it("scales by 100 to 1dp with a percent sign", () => {
    expect(pct(0.1234)).toBe("12.3%");
    expect(pct(-0.5)).toBe("-50.0%");
    expect(pct(0)).toBe("0.0%");
  });
});

describe("fmtIndicatorValue", () => {
  it("formats null as n/a regardless of unit", () => {
    expect(fmtIndicatorValue(null, "usd")).toBe("n/a");
  });

  it("delegates pct unit to pct()", () => {
    expect(fmtIndicatorValue(0.256, "pct")).toBe("25.6%");
  });

  it("formats ratio unit to 2dp", () => {
    expect(fmtIndicatorValue(1.256, "ratio")).toBe("1.26");
  });

  it("formats usd unit with sign + scaled suffix, trailing zero stripped", () => {
    expect(fmtIndicatorValue(1.5e12, "usd")).toBe("$1.5T");
    expect(fmtIndicatorValue(2.3e9, "usd")).toBe("$2.3B");
    expect(fmtIndicatorValue(4.567e6, "usd")).toBe("$4.6M");
    expect(fmtIndicatorValue(1500, "usd")).toBe("$1.5K");
    expect(fmtIndicatorValue(-2500, "usd")).toBe("-$2.5K");
    expect(fmtIndicatorValue(500, "usd")).toBe("$500");
  });

  it("formats count unit as thousands, 0dp", () => {
    expect(fmtIndicatorValue(1234.6, "count")).toBe("1,235");
  });

  it("falls back to thousands 0dp for unknown units", () => {
    expect(fmtIndicatorValue(1234.6, "unknown-unit")).toBe("1,235");
  });
});
