import { fireEvent, render, screen } from "@testing-library/react";
import ResizableSplit from "./ResizableSplit";

describe("ResizableSplit", () => {
  function renderSplit(props: Partial<React.ComponentProps<typeof ResizableSplit>> = {}) {
    return render(
      <div style={{ height: 800 }}>
        <ResizableSplit
          top={<div data-testid="top">top content</div>}
          bottom={<div data-testid="bottom">bottom content</div>}
          initialBottom={200}
          minBottom={50}
          maxBottom={600}
          {...props}
        />
      </div>,
    );
  }

  it("renders top and bottom slot content", () => {
    renderSplit();
    expect(screen.getByTestId("top")).toBeInTheDocument();
    expect(screen.getByTestId("bottom")).toBeInTheDocument();
  });

  it("exposes a separator role with min/max value bounds", () => {
    renderSplit();
    const handle = screen.getByRole("separator");
    expect(handle).toHaveAttribute("aria-valuemin", "50");
    expect(handle).toHaveAttribute("aria-valuemax", "600");
    expect(handle).toHaveAttribute("aria-valuenow", "200");
  });

  it("ArrowUp increases bottom height in 16px steps; ArrowDown decreases", () => {
    renderSplit({ initialBottom: 200 });
    const handle = screen.getByRole("separator");

    fireEvent.keyDown(handle, { key: "ArrowUp" });
    expect(handle).toHaveAttribute("aria-valuenow", "216");

    fireEvent.keyDown(handle, { key: "ArrowDown" });
    expect(handle).toHaveAttribute("aria-valuenow", "200");
  });

  it("PageUp/PageDown move in 64px steps", () => {
    renderSplit({ initialBottom: 200 });
    const handle = screen.getByRole("separator");

    fireEvent.keyDown(handle, { key: "PageUp" });
    expect(handle).toHaveAttribute("aria-valuenow", "264");

    fireEvent.keyDown(handle, { key: "PageDown" });
    expect(handle).toHaveAttribute("aria-valuenow", "200");
  });

  it("clamps to minBottom on ArrowDown past the floor", () => {
    renderSplit({ initialBottom: 60, minBottom: 50, maxBottom: 600 });
    const handle = screen.getByRole("separator");

    fireEvent.keyDown(handle, { key: "ArrowDown" });
    fireEvent.keyDown(handle, { key: "ArrowDown" });
    expect(handle).toHaveAttribute("aria-valuenow", "50");
  });

  it("clamps to maxBottom on ArrowUp past the ceiling", () => {
    renderSplit({ initialBottom: 590, minBottom: 50, maxBottom: 600 });
    const handle = screen.getByRole("separator");

    fireEvent.keyDown(handle, { key: "ArrowUp" });
    expect(handle).toHaveAttribute("aria-valuenow", "600");
  });
});
