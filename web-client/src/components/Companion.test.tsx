import { act, render, screen } from "@testing-library/react";
import Companion from "./Companion";

it("restores prior inert states only on owned management and navigation elements", () => {
  let matches = true;
  let resize = () => {};
  vi.stubGlobal("matchMedia", () => ({
    get matches() {
      return matches;
    },
    addEventListener: (_: string, callback: () => void) => {
      resize = callback;
    },
    removeEventListener: vi.fn(),
  }));
  const management = document.createElement("main");
  management.className = "management";
  management.inert = true;
  const sidebar = document.createElement("aside");
  sidebar.className = "app-sidebar";
  sidebar.inert = false;
  const unrelated = document.createElement("div");
  unrelated.inert = true;
  const shell = document.createElement("div");
  shell.className = "app-shell";
  const host = document.createElement("div");
  shell.append(management, sidebar, host);
  document.body.append(shell, unrelated);
  const props = {
    onOpen: vi.fn(),
    context: "Kitchen",
    route: "Helper",
    state: "Ready",
    connection: <button>Connect</button>,
    children: <button>Talk</button>,
  };
  const view = render(<Companion {...props} open />, { container: host });
  expect(management.inert).toBe(true);
  expect(sidebar.inert).toBe(true);
  expect(unrelated.inert).toBe(true);
  expect(screen.getByText("Viewing Kitchen")).toBeInTheDocument();
  act(() => {
    matches = false;
    resize();
  });
  expect(management.inert).toBe(true);
  expect(sidebar.inert).toBe(false);
  act(() => {
    matches = true;
    resize();
  });
  view.rerender(<Companion {...props} open={false} />);
  expect(management.inert).toBe(true);
  expect(sidebar.inert).toBe(false);
  expect(unrelated.inert).toBe(true);
  view.unmount();
  expect(management.inert).toBe(true);
  shell.remove();
  unrelated.remove();
  vi.unstubAllGlobals();
});

it("does not trap focus on controls inside collapsed advanced details", async () => {
  vi.stubGlobal("matchMedia", () => ({
    matches: true,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
  const rectangles = vi
    .spyOn(HTMLElement.prototype, "getClientRects")
    .mockReturnValue([{}] as unknown as DOMRectList);
  const view = render(
    <Companion
      open
      onOpen={vi.fn()}
      context="Devices"
      route="Shared helper"
      state="Ready"
      connection={
        <details>
          <summary>Advanced connection</summary>
          <button>Hidden re-enrollment</button>
        </details>
      }
    >
      <button>Start voice</button>
    </Companion>,
  );
  const close = screen.getByRole("button", {
    name: "Collapse voice companion",
  });
  close.focus();
  const { fireEvent } = await import("@testing-library/react");
  fireEvent.keyDown(close, { key: "Tab", shiftKey: true });
  expect(screen.getByText("Advanced connection")).toHaveFocus();
  view.unmount();
  rectangles.mockRestore();
  vi.unstubAllGlobals();
});
