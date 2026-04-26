import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TalkPanel from "./TalkPanel";

function renderPanel(overrides: Partial<React.ComponentProps<typeof TalkPanel>> = {}) {
  const handlers = {
    onTalkStart: vi.fn(),
    onTalkEnd: vi.fn(),
    onSetVolume: vi.fn(),
    onToggleMute: vi.fn(),
    onSetTalkMode: vi.fn(),
    onInterrupt: vi.fn(),
  };
  const props: React.ComponentProps<typeof TalkPanel> = {
    connectionState: "connected",
    isConnected: true,
    micUnavailable: false,
    talking: false,
    followUpListening: false,
    inputLevel: 0,
    outputVolume: 0.5,
    outputMuted: false,
    talkMode: "hold",
    latencyMs: null,
    ...handlers,
    ...overrides,
  };
  render(<TalkPanel {...props} />);
  return handlers;
}

describe("TalkPanel", () => {
  it("renders helper text for the connected/idle state", () => {
    renderPanel({ connectionState: "connected" });
    expect(screen.getByTestId("talk-helper")).toHaveTextContent(/hold the button/i);
  });

  it("renders helper text for the listening state", () => {
    renderPanel({ connectionState: "listening", talking: true });
    expect(screen.getByTestId("talk-helper")).toHaveTextContent(/listening/i);
  });

  it("shows the disconnected helper when not connected", () => {
    renderPanel({ connectionState: "disconnected", isConnected: false });
    expect(screen.getByTestId("talk-helper")).toHaveTextContent(/connect/i);
  });

  it("renders the Interrupt button only while speaking", () => {
    const handlers = renderPanel({ connectionState: "speaking" });
    const btn = screen.getByRole("button", { name: /interrupt/i });
    expect(btn).toBeInTheDocument();
    btn.click();
    expect(handlers.onInterrupt).toHaveBeenCalledTimes(1);
  });

  it("hides Interrupt when not speaking", () => {
    renderPanel({ connectionState: "connected" });
    expect(screen.queryByRole("button", { name: /interrupt/i })).not.toBeInTheDocument();
  });

  it("highlights the active talk mode and switches when clicked", async () => {
    const user = userEvent.setup();
    const handlers = renderPanel({ talkMode: "hold" });

    const radios = screen.getAllByRole("radio");
    expect(radios[0]).toHaveAttribute("aria-checked", "true");
    expect(radios[1]).toHaveAttribute("aria-checked", "false");

    await user.click(radios[1]);
    expect(handlers.onSetTalkMode).toHaveBeenCalledWith("toggle");
  });

  it("renders the latency value when available", () => {
    renderPanel({ latencyMs: 412 });
    expect(screen.getByText("412 ms")).toBeInTheDocument();
  });

  it("renders an em-dash when latency is null", () => {
    renderPanel({ latencyMs: null });
    const latencyLabel = screen.getByText(/^latency$/i);
    const latencyValue = latencyLabel.parentElement!.querySelector("span:last-child");
    expect(latencyValue).toHaveTextContent("—");
  });

  it("propagates volume/mute callbacks to the slider", async () => {
    const user = userEvent.setup();
    const handlers = renderPanel({ outputMuted: false });
    await user.click(screen.getByRole("button", { name: /mute output/i }));
    expect(handlers.onToggleMute).toHaveBeenCalledTimes(1);
  });
});
