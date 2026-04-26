import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import EventLog from "./EventLog";
import type { LogEntry } from "../hooks/useWebSocket";

function makeEntries(): LogEntry[] {
  return [
    { ts: 1700000000000, dir: "tx", text: "outgoing message" },
    { ts: 1700000001000, dir: "rx", text: "incoming message" },
    { ts: 1700000002000, dir: "sys", text: "system note" },
  ];
}

describe("EventLog", () => {
  it("renders no <header> element", () => {
    render(<EventLog entries={makeEntries()} />);
    expect(document.querySelector("header")).toBeNull();
  });

  it("does not render an 'Event Log' title or entry count", () => {
    render(<EventLog entries={makeEntries()} />);
    expect(screen.queryByText(/event log/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/entries/i)).not.toBeInTheDocument();
  });

  it("renders empty-state message when there are no entries", () => {
    render(<EventLog entries={[]} />);
    expect(screen.getByText(/no events yet/i)).toBeInTheDocument();
  });

  it("renders TX/RX/SYS prefixes for entries", () => {
    render(<EventLog entries={makeEntries()} />);
    expect(screen.getByText("TX")).toBeInTheDocument();
    expect(screen.getByText("RX")).toBeInTheDocument();
    expect(screen.getByText("SYS")).toBeInTheDocument();
    expect(screen.getByText("outgoing message")).toBeInTheDocument();
    expect(screen.getByText("incoming message")).toBeInTheDocument();
    expect(screen.getByText("system note")).toBeInTheDocument();
  });

  it("uses minimal padding on the scroll body", () => {
    const { container } = render(<EventLog entries={[]} />);
    const scrollBody = container.querySelector(".overflow-y-auto");
    expect(scrollBody).not.toBeNull();
    expect(scrollBody!.className).toMatch(/\bpx-3\b/);
    expect(scrollBody!.className).toMatch(/\bpy-1\b/);
  });

  it("hides the Clear button when there are no entries", () => {
    const onClear = vi.fn();
    render(<EventLog entries={[]} onClear={onClear} />);
    expect(screen.queryByRole("button", { name: /clear/i })).not.toBeInTheDocument();
  });

  it("shows the Clear button only when there are entries and onClear is provided", () => {
    const onClear = vi.fn();
    render(<EventLog entries={makeEntries()} onClear={onClear} />);
    expect(screen.getByRole("button", { name: /clear/i })).toBeInTheDocument();
  });

  it("does not render Clear if onClear is omitted", () => {
    render(<EventLog entries={makeEntries()} />);
    expect(screen.queryByRole("button", { name: /clear/i })).not.toBeInTheDocument();
  });

  it("calls onClear when Clear is clicked", async () => {
    const user = userEvent.setup();
    const onClear = vi.fn();
    render(<EventLog entries={makeEntries()} onClear={onClear} />);
    await user.click(screen.getByRole("button", { name: /clear/i }));
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it("scrolls to the bottom when entries change", () => {
    const scrollSpy = vi.spyOn(Element.prototype, "scrollIntoView");
    const { rerender } = render(<EventLog entries={[]} />);
    rerender(<EventLog entries={makeEntries()} />);
    expect(scrollSpy).toHaveBeenCalled();
    scrollSpy.mockRestore();
  });
});
