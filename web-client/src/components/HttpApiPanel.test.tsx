import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ConnectionState, LogEntry } from "../hooks/useWebSocket";
import HttpApiPanel from "./HttpApiPanel";

const mockListDevices = vi.fn();
const mockAnnounce = vi.fn();
const mockCommand = vi.fn();

// Return a stable object reference so the component's useCallback deps don't
// change on every render (mirroring the real hook's useCallback memoisation).
const stableApi = {
  listDevices: mockListDevices,
  announce: mockAnnounce,
  command: mockCommand,
};

vi.mock("../hooks/useHttpApi", () => ({
  deriveHttpUrl: vi.fn(() => "http://localhost:8080"),
  useHttpApi: vi.fn(() => stableApi),
}));

beforeEach(() => {
  vi.clearAllMocks();
  mockListDevices.mockResolvedValue([]);
});

function renderPanel(
  overrides: {
    wsState?: ConnectionState;
    addLog?: (dir: LogEntry["dir"], text: string) => void;
  } = {},
) {
  const props = {
    wsUrl: "ws://localhost:8765",
    token: "tok",
    wsState: overrides.wsState ?? "connected",
    addLog: overrides.addLog ?? vi.fn(),
  };
  return render(<HttpApiPanel {...props} />);
}

const DEVICES = [
  { id: "d1", name: "Living Room", state: "idle", lastSeen: "2026-01-01" },
  { id: "d2", name: "Kitchen", state: "listening", lastSeen: "2026-01-01" },
];

/** Wait for the device list to render by checking for a list item. */
async function waitForDevices() {
  await waitFor(() => {
    expect(screen.getByRole("list")).toBeInTheDocument();
    expect(within(screen.getByRole("list")).getAllByRole("listitem").length).toBeGreaterThan(0);
  });
}

describe("HttpApiPanel", () => {
  describe("rendering", () => {
    it("renders a connect-prompt placeholder when wsState === 'disconnected'", () => {
      renderPanel({ wsState: "disconnected" });
      expect(screen.getByText(/connect to a server/i)).toBeInTheDocument();
      expect(screen.queryByText("HTTP API")).not.toBeInTheDocument();
    });

    it("renders when wsState === 'connected'", () => {
      renderPanel({ wsState: "connected" });
      expect(screen.getByText("HTTP API")).toBeInTheDocument();
    });
  });

  describe("device list", () => {
    it("calls listDevices on mount", async () => {
      renderPanel();
      await waitFor(() => expect(mockListDevices).toHaveBeenCalledTimes(1));
    });

    it("renders device names and state badges", async () => {
      mockListDevices.mockResolvedValue(DEVICES);
      renderPanel();
      await waitForDevices();

      const list = screen.getByRole("list");
      const items = within(list).getAllByRole("listitem");
      expect(items).toHaveLength(2);

      expect(within(items[0]).getByText("Living Room")).toBeInTheDocument();
      expect(within(items[0]).getByText("idle")).toBeInTheDocument();
      expect(within(items[1]).getByText("Kitchen")).toBeInTheDocument();
      expect(within(items[1]).getByText("listening")).toBeInTheDocument();
    });

    it("shows 'No devices' when list is empty", async () => {
      mockListDevices.mockResolvedValue([]);
      renderPanel();

      await waitFor(() => {
        expect(screen.getByText("No devices")).toBeInTheDocument();
      });
    });

    it("shows error message when listDevices throws", async () => {
      mockListDevices.mockRejectedValue(new Error("Network error"));
      renderPanel();

      await waitFor(() => {
        expect(screen.getByText("Network error")).toBeInTheDocument();
      });
    });

    it("clicking 'Refresh ↺' calls listDevices again", async () => {
      const user = userEvent.setup();
      mockListDevices.mockResolvedValue([]);
      renderPanel();

      await waitFor(() => expect(mockListDevices).toHaveBeenCalledTimes(1));

      const refreshBtn = screen.getByText(/Refresh/);
      await user.click(refreshBtn);

      await waitFor(() => expect(mockListDevices).toHaveBeenCalledTimes(2));
    });

    it("clicking 'Announce' on a device row pre-fills the announce device dropdown", async () => {
      const user = userEvent.setup();
      mockListDevices.mockResolvedValue(DEVICES);
      renderPanel();
      await waitForDevices();

      const list = screen.getByRole("list");
      const items = within(list).getAllByRole("listitem");
      // Click the "Announce" button on the Kitchen row (second item)
      const kitchenAnnounceBtn = within(items[1]).getByRole("button", { name: "Announce" });
      await user.click(kitchenAnnounceBtn);

      // Find the announce section's device select — it's the first select after the "Announce" heading
      const selects = screen.getAllByRole("combobox");
      // The announce device select is the first one (index 0 = announce device, 1 = control device, 2 = command)
      expect((selects[0] as HTMLSelectElement).value).toBe("d2");
    });
  });

  describe("announce form", () => {
    beforeEach(() => {
      mockListDevices.mockResolvedValue(DEVICES);
    });

    it("renders device dropdown populated from device list", async () => {
      renderPanel();

      await waitFor(() => {
        const options = screen.getAllByRole("option");
        const optionTexts = options.map((o) => o.textContent);
        expect(optionTexts).toContain("Living Room");
        expect(optionTexts).toContain("Kitchen");
      });
    });

    it("send button calls api.announce(deviceId, text)", async () => {
      const user = userEvent.setup();
      mockAnnounce.mockResolvedValue(undefined);
      renderPanel();
      await waitForDevices();

      const textInput = screen.getByPlaceholderText("Hello from the browser");
      await user.type(textInput, "Test message");

      const sendButtons = screen.getAllByRole("button", { name: "Send" });
      await user.click(sendButtons[0]); // First Send is in announce section

      await waitFor(() => {
        expect(mockAnnounce).toHaveBeenCalledWith("d1", "Test message");
      });
    });

    it("calls addLog with success message on success", async () => {
      const user = userEvent.setup();
      const addLog = vi.fn();
      mockAnnounce.mockResolvedValue(undefined);
      renderPanel({ addLog });
      await waitForDevices();

      const textInput = screen.getByPlaceholderText("Hello from the browser");
      await user.type(textInput, "Hello");

      const sendButtons = screen.getAllByRole("button", { name: "Send" });
      await user.click(sendButtons[0]);

      await waitFor(() => {
        expect(addLog).toHaveBeenCalledWith("sys", expect.stringContaining("Announce sent"));
      });
    });

    it("shows inline error and calls addLog on failure", async () => {
      const user = userEvent.setup();
      const addLog = vi.fn();
      mockAnnounce.mockRejectedValue(new Error("Device offline"));
      renderPanel({ addLog });
      await waitForDevices();

      const textInput = screen.getByPlaceholderText("Hello from the browser");
      await user.type(textInput, "Hello");

      const sendButtons = screen.getAllByRole("button", { name: "Send" });
      await user.click(sendButtons[0]);

      await waitFor(() => {
        expect(screen.getByText("Device offline")).toBeInTheDocument();
        expect(addLog).toHaveBeenCalledWith("sys", expect.stringContaining("Device offline"));
      });
    });

    it("clears text input after successful send", async () => {
      const user = userEvent.setup();
      mockAnnounce.mockResolvedValue(undefined);
      renderPanel();
      await waitForDevices();

      const textInput = screen.getByPlaceholderText("Hello from the browser") as HTMLInputElement;
      await user.type(textInput, "Hello");
      expect(textInput.value).toBe("Hello");

      const sendButtons = screen.getAllByRole("button", { name: "Send" });
      await user.click(sendButtons[0]);

      await waitFor(() => {
        expect(textInput.value).toBe("");
      });
    });
  });

  describe("control form", () => {
    beforeEach(() => {
      mockListDevices.mockResolvedValue(DEVICES);
    });

    /** Get the command select (the one containing "mute" option). */
    function getCommandSelect(): HTMLSelectElement {
      const selects = screen.getAllByRole("combobox");
      return selects.find((s) => {
        const options = s.querySelectorAll("option");
        return Array.from(options).some((o) => o.value === "mute");
      })! as HTMLSelectElement;
    }

    it("volume input is visible when command is set_volume", async () => {
      renderPanel();
      await waitForDevices();

      // Default command is set_volume
      expect(screen.getByLabelText("Volume")).toBeInTheDocument();
    });

    it("volume input is hidden for mute, unmute, reboot", async () => {
      const user = userEvent.setup();
      renderPanel();
      await waitForDevices();

      const cmdSelect = getCommandSelect();

      await user.selectOptions(cmdSelect, "mute");
      expect(screen.queryByLabelText("Volume")).not.toBeInTheDocument();

      await user.selectOptions(cmdSelect, "unmute");
      expect(screen.queryByLabelText("Volume")).not.toBeInTheDocument();

      await user.selectOptions(cmdSelect, "reboot");
      expect(screen.queryByLabelText("Volume")).not.toBeInTheDocument();
    });

    it("send button calls api.command(deviceId, 'mute') without params for mute", async () => {
      const user = userEvent.setup();
      mockCommand.mockResolvedValue(undefined);
      renderPanel();
      await waitForDevices();

      const cmdSelect = getCommandSelect();
      await user.selectOptions(cmdSelect, "mute");

      const sendButtons = screen.getAllByRole("button", { name: "Send" });
      await user.click(sendButtons[sendButtons.length - 1]);

      await waitFor(() => {
        expect(mockCommand).toHaveBeenCalledWith("d1", "mute", undefined);
      });
    });

    it("send button calls api.command(deviceId, 'set_volume', { volume: 50 }) for set_volume", async () => {
      const user = userEvent.setup();
      mockCommand.mockResolvedValue(undefined);
      renderPanel();
      await waitForDevices();

      // Default command is set_volume, default volume is 50
      const sendButtons = screen.getAllByRole("button", { name: "Send" });
      await user.click(sendButtons[sendButtons.length - 1]);

      await waitFor(() => {
        expect(mockCommand).toHaveBeenCalledWith("d1", "set_volume", { volume: 50 });
      });
    });

    it("shows inline error on failure", async () => {
      const user = userEvent.setup();
      mockCommand.mockRejectedValue(new Error("Command failed"));
      renderPanel();
      await waitForDevices();

      const sendButtons = screen.getAllByRole("button", { name: "Send" });
      await user.click(sendButtons[sendButtons.length - 1]);

      await waitFor(() => {
        expect(screen.getByText("Command failed")).toBeInTheDocument();
      });
    });

    it("calls addLog with success message on success", async () => {
      const user = userEvent.setup();
      const addLog = vi.fn();
      mockCommand.mockResolvedValue(undefined);
      renderPanel({ addLog });
      await waitForDevices();

      const sendButtons = screen.getAllByRole("button", { name: "Send" });
      await user.click(sendButtons[sendButtons.length - 1]);

      await waitFor(() => {
        expect(addLog).toHaveBeenCalledWith("sys", expect.stringContaining("Command"));
      });
    });
  });
});
