import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import AgentCapabilities from "./AgentCapabilities";

describe("integration capability boundaries", () => {
  it("does not infer capabilities from missing metadata", () => {
    render(<AgentCapabilities />);
    expect(screen.getByText(/No UI capabilities are assumed/)).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });
  it("distinguishes configured routes from support and permission", () => {
    render(<AgentCapabilities metadata={{ framework: "openclaw", configuration: "enrolled", capabilities: [
      { id: "ui.agent.name.propose", supported: false, available: false, reason: "transport_unavailable" },
    ] }} />);
    expect(screen.getByText(/Enrolled connection/)).toBeTruthy();
    expect(screen.getByText(/UI capability transport is not implemented/)).toBeTruthy();
    expect(screen.getByText(/not permission/)).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
