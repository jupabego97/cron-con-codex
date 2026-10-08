import { describe, expect, it } from "vitest";

import { money, number, percent } from "./format";

describe("number", () => {
  it("distinguishes unknown values from a measured zero", () => {
    expect(number(null)).toBe("—");
    expect(number(0)).toBe("0");
  });
  it("never turns unknown margin or a nonfinite amount into a measured zero", () => {
    expect(money(null)).toBe("—");
    expect(money(Infinity)).toBe("—");
    expect(percent(null)).toBe("—");
    expect(percent(0)).toBe("0%");
  });
});
