import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";

// jsdom doesn't implement these; stub them so layout primitives don't crash.
beforeAll(() => {
  if (typeof window.ResizeObserver === "undefined") {
    // @ts-expect-error mocking out ResizeObserver
    window.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
  if (typeof window.scrollTo === "undefined") {
    window.scrollTo = vi.fn();
  }
  // jsdom doesn't implement scrollIntoView
  Element.prototype.scrollIntoView = vi.fn();
});

describe("App shell", () => {
  it("renders the three-column shell with sidebar, main, and talk panel", () => {
    render(<App />);
    expect(screen.getByRole("complementary", { name: /primary navigation/i })).toBeInTheDocument();
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: /talk panel/i })).toBeInTheDocument();
  });

  it("defaults to the Connection section", () => {
    render(<App />);
    const sidebar = screen.getByRole("complementary", { name: /primary navigation/i });
    expect(within(sidebar).getByRole("button", { name: /connection/i })).toHaveAttribute(
      "aria-current",
      "page",
    );
    // Connection section heading lives in the main content
    const main = screen.getByRole("main");
    expect(within(main).getByText("Connection")).toBeInTheDocument();
  });

  it("clicking a nav item swaps the main section content", async () => {
    const user = userEvent.setup();
    render(<App />);
    const sidebar = screen.getByRole("complementary", { name: /primary navigation/i });
    const main = screen.getByRole("main");

    await user.click(within(sidebar).getByRole("button", { name: /settings/i }));
    expect(within(main).getByText(/coming soon/i)).toBeInTheDocument();

    await user.click(within(sidebar).getByRole("button", { name: /channels/i }));
    expect(within(main).getByText(/connect to a server to manage channels/i)).toBeInTheDocument();

    await user.click(within(sidebar).getByRole("button", { name: /devices/i }));
    expect(within(main).getByText(/connect to a server to manage devices/i)).toBeInTheDocument();
  });

  it("does not include the legacy 'HTTP API' nav entry", () => {
    render(<App />);
    const sidebar = screen.getByRole("complementary", { name: /primary navigation/i });
    expect(within(sidebar).queryByRole("button", { name: /http api/i })).not.toBeInTheDocument();
  });

  it("renders the resize handle between top and bottom panes", () => {
    render(<App />);
    expect(screen.getByRole("separator", { name: /resize/i })).toBeInTheDocument();
  });

  it("renders the talk panel with the disconnected helper text initially", () => {
    render(<App />);
    expect(screen.getByTestId("talk-helper")).toHaveTextContent(/connect/i);
  });

  it("renders the event log empty-state message in the bottom pane", () => {
    render(<App />);
    expect(screen.getByText(/no events yet/i)).toBeInTheDocument();
  });
});
