import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import VolumeSlider from "./VolumeSlider";

describe("VolumeSlider", () => {
  function renderSlider(
    overrides: Partial<React.ComponentProps<typeof VolumeSlider>> = {},
  ) {
    const onChange = vi.fn();
    const onToggleMute = vi.fn();
    render(
      <VolumeSlider
        value={0.5}
        muted={false}
        onChange={onChange}
        onToggleMute={onToggleMute}
        {...overrides}
      />,
    );
    return { onChange, onToggleMute };
  }

  it("renders the percent readout based on value", () => {
    renderSlider({ value: 0.65 });
    expect(screen.getByText("65%")).toBeInTheDocument();
  });

  it("renders 0% when muted regardless of value", () => {
    renderSlider({ value: 0.8, muted: true });
    expect(screen.getByText("0%")).toBeInTheDocument();
  });

  it("the range input reflects value as 0..100", () => {
    renderSlider({ value: 0.4 });
    const range = screen.getByRole("slider") as HTMLInputElement;
    expect(range.value).toBe("40");
    expect(range.min).toBe("0");
    expect(range.max).toBe("100");
  });

  it("disables the range input when muted", () => {
    renderSlider({ muted: true });
    const range = screen.getByRole("slider") as HTMLInputElement;
    expect(range).toBeDisabled();
  });

  it("clicking the icon button calls onToggleMute", async () => {
    const user = userEvent.setup();
    const { onToggleMute } = renderSlider();
    await user.click(screen.getByRole("button", { name: /mute output/i }));
    expect(onToggleMute).toHaveBeenCalledTimes(1);
  });

  it("the mute button announces its state via aria-pressed", () => {
    const { unmount } = render(
      <VolumeSlider value={0.5} muted={false} onChange={() => {}} onToggleMute={() => {}} />,
    );
    expect(screen.getByRole("button", { name: /mute output/i })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    unmount();

    render(
      <VolumeSlider value={0.5} muted={true} onChange={() => {}} onToggleMute={() => {}} />,
    );
    expect(screen.getByRole("button", { name: /unmute output/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("changing the slider calls onChange with a 0..1 value", () => {
    const onChange = vi.fn();
    render(
      <VolumeSlider
        value={0.5}
        muted={false}
        onChange={onChange}
        onToggleMute={() => {}}
      />,
    );
    const range = screen.getByRole("slider") as HTMLInputElement;
    fireEvent.change(range, { target: { value: "75" } });
    expect(onChange).toHaveBeenCalledWith(0.75);
  });
});
