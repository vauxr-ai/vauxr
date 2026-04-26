import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Sidebar from "./Sidebar";

describe("Sidebar", () => {
  function renderSidebar(overrides: Partial<React.ComponentProps<typeof Sidebar>> = {}) {
    const onSelect = vi.fn();
    const props = {
      active: "connection" as const,
      onSelect,
      connectionState: "disconnected" as const,
      deviceId: "",
      ...overrides,
    };
    render(<Sidebar {...props} />);
    return { onSelect };
  }

  it("renders all five nav items", () => {
    renderSidebar();
    expect(screen.getByRole("button", { name: /connection/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /channels/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /devices/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /http api/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /settings/i })).toBeInTheDocument();
  });

  it("marks the active item with aria-current=page", () => {
    renderSidebar({ active: "channels" });
    const channels = screen.getByRole("button", { name: /channels/i });
    expect(channels).toHaveAttribute("aria-current", "page");

    const connection = screen.getByRole("button", { name: /connection/i });
    expect(connection).not.toHaveAttribute("aria-current");
  });

  it("calls onSelect with the section id when a nav item is clicked", async () => {
    const user = userEvent.setup();
    const { onSelect } = renderSidebar();

    await user.click(screen.getByRole("button", { name: /devices/i }));
    expect(onSelect).toHaveBeenCalledWith("devices");

    await user.click(screen.getByRole("button", { name: /http api/i }));
    expect(onSelect).toHaveBeenCalledWith("api");
  });

  it("shows 'Disconnected' pill when state is disconnected", () => {
    renderSidebar({ connectionState: "disconnected" });
    expect(screen.getByText(/disconnected/i)).toBeInTheDocument();
    expect(screen.queryByText("test-device")).not.toBeInTheDocument();
  });

  it("shows 'Connected' pill and device id when state is connected", () => {
    renderSidebar({ connectionState: "connected", deviceId: "test-device" });
    expect(screen.getByText(/^connected$/i)).toBeInTheDocument();
    expect(screen.getByText("test-device")).toBeInTheDocument();
  });

  it("treats listening/processing/speaking as connected for the pill", () => {
    for (const state of ["listening", "processing", "speaking"] as const) {
      const { unmount } = render(
        <Sidebar
          active="connection"
          onSelect={() => {}}
          connectionState={state}
          deviceId="dev"
        />,
      );
      expect(screen.getByText(/^connected$/i)).toBeInTheDocument();
      unmount();
    }
  });
});
