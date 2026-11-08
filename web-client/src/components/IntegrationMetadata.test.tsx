import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import IntegrationMetadata from "./IntegrationMetadata";

describe("integration configuration provenance", () => {
  it("handles missing metadata", () => {
    render(<IntegrationMetadata />);
    expect(screen.getByText(/source is unavailable/)).toBeTruthy();
  });
  it.each([
    ["enrolled", "Enrolled connection"],
    ["operator-managed", "Operator-managed connection"],
    ["registered", "Registered route"],
  ] as const)("describes %s configuration", (configuration, label) => {
    render(<IntegrationMetadata metadata={{ framework: "openclaw", configuration }} />);
    expect(screen.getByText(`OpenClaw · ${label}`)).toBeTruthy();
    expect(screen.getByText(/does not prove connectivity/)).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("list")).toBeNull();
  });
});
