import { render, screen } from "@testing-library/react";
import MicLevelMeter from "./MicLevelMeter";

describe("MicLevelMeter", () => {
  it("renders 8 segments by default", () => {
    render(<MicLevelMeter level={0} />);
    expect(screen.getAllByTestId("mic-level-segment")).toHaveLength(8);
  });

  it("lights no segments when inactive, regardless of level", () => {
    render(<MicLevelMeter level={1} active={false} />);
    const segments = screen.getAllByTestId("mic-level-segment");
    for (const s of segments) {
      expect(s).toHaveAttribute("data-lit", "false");
    }
  });

  it("lights ~half the segments at level 0.5 when active", () => {
    render(<MicLevelMeter level={0.5} active />);
    const segments = screen.getAllByTestId("mic-level-segment");
    const lit = segments.filter((s) => s.getAttribute("data-lit") === "true");
    expect(lit).toHaveLength(4);
  });

  it("lights all segments at level 1 when active", () => {
    render(<MicLevelMeter level={1} active />);
    const segments = screen.getAllByTestId("mic-level-segment");
    const lit = segments.filter((s) => s.getAttribute("data-lit") === "true");
    expect(lit).toHaveLength(8);
  });

  it("renders an em-dash for the dB readout when inactive", () => {
    render(<MicLevelMeter level={0.5} active={false} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("shows a dB readout when active and level > 0", () => {
    render(<MicLevelMeter level={0.5} active />);
    expect(screen.getByText(/dB$/)).toBeInTheDocument();
  });

  it("exposes role=meter with aria-valuenow", () => {
    render(<MicLevelMeter level={0.5} active />);
    const meter = screen.getByRole("meter");
    expect(meter).toHaveAttribute("aria-valuenow", "0.5");
    expect(meter).toHaveAttribute("aria-valuemin", "0");
    expect(meter).toHaveAttribute("aria-valuemax", "1");
  });

  it("clamps level out of range", () => {
    const { rerender } = render(<MicLevelMeter level={2} active />);
    let segments = screen.getAllByTestId("mic-level-segment");
    expect(segments.filter((s) => s.getAttribute("data-lit") === "true")).toHaveLength(8);

    rerender(<MicLevelMeter level={-1} active />);
    segments = screen.getAllByTestId("mic-level-segment");
    expect(segments.filter((s) => s.getAttribute("data-lit") === "true")).toHaveLength(0);
  });
});
