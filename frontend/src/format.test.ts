import { describe, expect, it } from "vitest";

import { number } from "./format";

describe("number", () => {
  it("formats null values as zero", () => {
    expect(number(null)).toBe("0");
  });
});
