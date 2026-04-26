import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ConnectionState, LogEntry } from "../hooks/useWebSocket";
import DevicesPanel from "./DevicesPanel";

const mockListDevices = vi.fn();
const mockAnnounce = vi.fn();
const mockCommand = vi.fn();

const stableApi = {
  listDevices: mockListDevices,
  announce: mockAnnounce,
  command: mockCommand,
};

vi.mock("../hooks/useHttpApi", () => ({
  deriveHttpUrl: vi.fn(() => "http://localhost:8080"),
  useHttpApi: vi.fn(() => stableApi),
}));

interface FakeDevice {
  id: string;
  name: string;
  state: string;
  lastSeen: string;
  config: {
    name?: string;
    voice?: boolean;
    follow_up_mode?: "auto" | "always" | "never";
    output_sample_rate?: number;
  };
}

const DEVICES: FakeDevice[] = [
  {
    id: "d1",
    name: "Living Room",
    state: "idle",
    lastSeen: "2026-01-01T12:00:00Z",
    config: { name: "Living Room", voice: true, follow_up_mode: "auto" },
  },
  {
    id: "d2",
    name: "Kitchen",
    state: "listening",
    lastSeen: "2026-01-01T12:00:00Z",
    config: { name: "Kitchen", voice: true, follow_up_mode: "always" },
  },
];

let fetchSpy: ReturnType<typeof vi.fn>;

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    statusText: ok ? "OK" : "Error",
    json: async () => body,
  } as unknown as Response;
}

beforeEach(() => {
  vi.clearAllMocks();
  fetchSpy = vi.fn().mockResolvedValue(jsonResponse(DEVICES));
  globalThis.fetch = fetchSpy as unknown as typeof fetch;
});

afterEach(() => {
  vi.restoreAllMocks();
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
  return render(<DevicesPanel {...props} />);
}

async function waitForDevices() {
  await waitFor(() => {
    expect(screen.getByRole("list")).toBeInTheDocument();
    expect(within(screen.getByRole("list")).getAllByRole("listitem").length).toBeGreaterThan(0);
  });
}

describe("DevicesPanel", () => {
  describe("rendering", () => {
    it("renders a connect-prompt placeholder when wsState === 'disconnected'", () => {
      renderPanel({ wsState: "disconnected" });
      expect(screen.getByText(/connect to a server to manage devices/i)).toBeInTheDocument();
      expect(screen.queryByText(/^Devices$/)).not.toBeInTheDocument();
    });

    it("renders the Devices header when connected", async () => {
      renderPanel();
      expect(screen.getByText(/^Devices$/)).toBeInTheDocument();
      await waitForDevices();
    });

    it("renders 'No devices yet' when the device list is empty", async () => {
      fetchSpy.mockResolvedValueOnce(jsonResponse([]));
      renderPanel();
      await waitFor(() => {
        expect(screen.getByText(/no devices yet/i)).toBeInTheDocument();
      });
    });

    it("shows error inline when refresh fails", async () => {
      fetchSpy.mockResolvedValueOnce(jsonResponse({ error: "Network down" }, false, 500));
      renderPanel();
      await waitFor(() => {
        expect(screen.getByText("Network down")).toBeInTheDocument();
      });
    });
  });

  describe("device cards", () => {
    it("renders a card per device with name, state pill, and id", async () => {
      renderPanel();
      await waitForDevices();
      const items = within(screen.getByRole("list")).getAllByRole("listitem");
      expect(items).toHaveLength(2);
      expect(within(items[0]).getByText("Living Room")).toBeInTheDocument();
      expect(within(items[0]).getByText("idle")).toBeInTheDocument();
      expect(within(items[0]).getByText(/d1/)).toBeInTheDocument();
      expect(within(items[1]).getByText("Kitchen")).toBeInTheDocument();
      expect(within(items[1]).getByText("listening")).toBeInTheDocument();
    });

    it("cards default to collapsed (config/actions hidden)", async () => {
      renderPanel();
      await waitForDevices();
      expect(screen.queryByText(/^Config$/)).not.toBeInTheDocument();
      expect(screen.queryByText(/^Announce$/)).not.toBeInTheDocument();
      expect(screen.queryByText(/^Control$/)).not.toBeInTheDocument();
    });

    it("clicking the card header expands it to show Config/Announce/Control", async () => {
      const user = userEvent.setup();
      renderPanel();
      await waitForDevices();

      const items = within(screen.getByRole("list")).getAllByRole("listitem");
      const toggle = within(items[0]).getByRole("button", { expanded: false });
      await user.click(toggle);

      expect(within(items[0]).getByRole("button", { expanded: true })).toBeInTheDocument();
      expect(within(items[0]).getByText(/^Config$/)).toBeInTheDocument();
      expect(within(items[0]).getByText(/^Announce$/)).toBeInTheDocument();
      expect(within(items[0]).getByText(/^Control$/)).toBeInTheDocument();
    });

    it("clicking the header again collapses the card", async () => {
      const user = userEvent.setup();
      renderPanel();
      await waitForDevices();

      const items = within(screen.getByRole("list")).getAllByRole("listitem");
      const toggle = within(items[0]).getByRole("button", { expanded: false });
      await user.click(toggle);
      expect(within(items[0]).getByText(/^Config$/)).toBeInTheDocument();

      const expandedToggle = within(items[0]).getByRole("button", { expanded: true });
      await user.click(expandedToggle);
      expect(within(items[0]).queryByText(/^Config$/)).not.toBeInTheDocument();
    });

    it("expands cards independently", async () => {
      const user = userEvent.setup();
      renderPanel();
      await waitForDevices();

      const items = within(screen.getByRole("list")).getAllByRole("listitem");
      await user.click(within(items[0]).getByRole("button", { expanded: false }));

      expect(within(items[0]).getByText(/^Config$/)).toBeInTheDocument();
      expect(within(items[1]).queryByText(/^Config$/)).not.toBeInTheDocument();
    });
  });

  describe("config section", () => {
    async function expandFirstCard(user: ReturnType<typeof userEvent.setup>) {
      await waitForDevices();
      const items = within(screen.getByRole("list")).getAllByRole("listitem");
      await user.click(within(items[0]).getByRole("button", { expanded: false }));
      return items[0];
    }

    it("changing follow-up mode triggers a PATCH and updates state", async () => {
      const user = userEvent.setup();
      const updated = {
        ...DEVICES[0],
        config: { ...DEVICES[0].config, follow_up_mode: "never" as const },
      };
      fetchSpy.mockResolvedValueOnce(jsonResponse(DEVICES));
      fetchSpy.mockResolvedValueOnce(jsonResponse(updated));

      const addLog = vi.fn();
      renderPanel({ addLog });
      const card = await expandFirstCard(user);

      const select = within(card).getByLabelText(/follow-up/i);
      await user.selectOptions(select, "never");

      await waitFor(() => {
        const patchCall = fetchSpy.mock.calls.find(
          (c) => typeof c[0] === "string" && (c[0] as string).includes("/api/devices/d1"),
        );
        expect(patchCall).toBeDefined();
        const init = patchCall![1] as RequestInit;
        expect(init.method).toBe("PATCH");
        expect(JSON.parse(init.body as string)).toEqual({ follow_up_mode: "never" });
      });
      await waitFor(() => {
        expect(addLog).toHaveBeenCalledWith("sys", expect.stringContaining("follow_up_mode → never"));
      });
    });

    it("changing voice toggle saves voice patch", async () => {
      const user = userEvent.setup();
      const updated = {
        ...DEVICES[0],
        config: { ...DEVICES[0].config, voice: false },
      };
      fetchSpy.mockResolvedValueOnce(jsonResponse(DEVICES));
      fetchSpy.mockResolvedValueOnce(jsonResponse(updated));

      renderPanel();
      const card = await expandFirstCard(user);

      const voiceCheckbox = within(card).getByRole("checkbox");
      expect(voiceCheckbox).toBeChecked();
      await user.click(voiceCheckbox);

      await waitFor(() => {
        const patchCall = fetchSpy.mock.calls.find(
          (c) => typeof c[0] === "string" && (c[0] as string).includes("/api/devices/d1"),
        );
        expect(patchCall).toBeDefined();
        expect(JSON.parse((patchCall![1] as RequestInit).body as string)).toEqual({ voice: false });
      });
    });

    it("name input saves on blur if changed", async () => {
      const user = userEvent.setup();
      fetchSpy.mockResolvedValueOnce(jsonResponse(DEVICES));
      fetchSpy.mockResolvedValueOnce(jsonResponse({
        ...DEVICES[0],
        config: { ...DEVICES[0].config, name: "Den" },
      }));

      renderPanel();
      const card = await expandFirstCard(user);

      const nameInput = within(card).getByLabelText(/name/i) as HTMLInputElement;
      await user.clear(nameInput);
      await user.type(nameInput, "Den");
      nameInput.blur();

      await waitFor(() => {
        const patchCall = fetchSpy.mock.calls.find(
          (c) => typeof c[0] === "string" && (c[0] as string).includes("/api/devices/d1"),
        );
        expect(patchCall).toBeDefined();
        expect(JSON.parse((patchCall![1] as RequestInit).body as string)).toEqual({ name: "Den" });
      });
    });
  });

  describe("announce action (per card)", () => {
    it("Send button calls api.announce with this device's id", async () => {
      const user = userEvent.setup();
      mockAnnounce.mockResolvedValue(undefined);
      const addLog = vi.fn();
      renderPanel({ addLog });
      await waitForDevices();
      const items = within(screen.getByRole("list")).getAllByRole("listitem");
      await user.click(within(items[1]).getByRole("button", { expanded: false }));

      const card = items[1];
      const text = within(card).getByPlaceholderText(/hello from the browser/i);
      await user.type(text, "Test");

      const sendButtons = within(card).getAllByRole("button", { name: "Send" });
      await user.click(sendButtons[0]);

      await waitFor(() => {
        expect(mockAnnounce).toHaveBeenCalledWith("d2", "Test");
        expect(addLog).toHaveBeenCalledWith("sys", expect.stringContaining("Announce sent to d2"));
      });
    });

    it("clears text input after a successful announce", async () => {
      const user = userEvent.setup();
      mockAnnounce.mockResolvedValue(undefined);
      renderPanel();
      await waitForDevices();
      const items = within(screen.getByRole("list")).getAllByRole("listitem");
      await user.click(within(items[0]).getByRole("button", { expanded: false }));

      const card = items[0];
      const text = within(card).getByPlaceholderText(/hello from the browser/i) as HTMLInputElement;
      await user.type(text, "Hello");
      expect(text.value).toBe("Hello");

      const sendButtons = within(card).getAllByRole("button", { name: "Send" });
      await user.click(sendButtons[0]);

      await waitFor(() => expect(text.value).toBe(""));
    });

    it("shows inline error and logs when announce fails", async () => {
      const user = userEvent.setup();
      mockAnnounce.mockRejectedValue(new Error("Device offline"));
      const addLog = vi.fn();
      renderPanel({ addLog });
      await waitForDevices();
      const items = within(screen.getByRole("list")).getAllByRole("listitem");
      await user.click(within(items[0]).getByRole("button", { expanded: false }));

      const card = items[0];
      const text = within(card).getByPlaceholderText(/hello from the browser/i);
      await user.type(text, "Hi");
      const sendButtons = within(card).getAllByRole("button", { name: "Send" });
      await user.click(sendButtons[0]);

      await waitFor(() => {
        expect(within(card).getByText("Device offline")).toBeInTheDocument();
        expect(addLog).toHaveBeenCalledWith("sys", expect.stringContaining("Device offline"));
      });
    });
  });

  describe("control action (per card)", () => {
    it("set_volume Send calls api.command with {volume} param", async () => {
      const user = userEvent.setup();
      mockCommand.mockResolvedValue(undefined);
      const addLog = vi.fn();
      renderPanel({ addLog });
      await waitForDevices();
      const items = within(screen.getByRole("list")).getAllByRole("listitem");
      await user.click(within(items[0]).getByRole("button", { expanded: false }));

      const card = items[0];
      const sendButtons = within(card).getAllByRole("button", { name: "Send" });
      // The control Send is the last "Send" button in the card
      await user.click(sendButtons[sendButtons.length - 1]);

      await waitFor(() => {
        expect(mockCommand).toHaveBeenCalledWith("d1", "set_volume", { volume: 50 });
        expect(addLog).toHaveBeenCalledWith("sys", expect.stringContaining("Command set_volume sent to d1"));
      });
    });

    it("hides volume input for mute/unmute/reboot and sends command without params", async () => {
      const user = userEvent.setup();
      mockCommand.mockResolvedValue(undefined);
      renderPanel();
      await waitForDevices();
      const items = within(screen.getByRole("list")).getAllByRole("listitem");
      await user.click(within(items[0]).getByRole("button", { expanded: false }));

      const card = items[0];
      const cmdSelect = within(card).getByLabelText(/command/i);
      await user.selectOptions(cmdSelect, "mute");
      expect(within(card).queryByLabelText(/volume/i)).not.toBeInTheDocument();

      const sendButtons = within(card).getAllByRole("button", { name: "Send" });
      await user.click(sendButtons[sendButtons.length - 1]);

      await waitFor(() => {
        expect(mockCommand).toHaveBeenCalledWith("d1", "mute", undefined);
      });
    });

    it("shows inline error when command fails", async () => {
      const user = userEvent.setup();
      mockCommand.mockRejectedValue(new Error("Command failed"));
      renderPanel();
      await waitForDevices();
      const items = within(screen.getByRole("list")).getAllByRole("listitem");
      await user.click(within(items[0]).getByRole("button", { expanded: false }));

      const card = items[0];
      const sendButtons = within(card).getAllByRole("button", { name: "Send" });
      await user.click(sendButtons[sendButtons.length - 1]);

      await waitFor(() => {
        expect(within(card).getByText("Command failed")).toBeInTheDocument();
      });
    });
  });

  describe("refresh", () => {
    it("clicking the single Refresh button re-fetches the device list", async () => {
      const user = userEvent.setup();
      renderPanel();
      await waitForDevices();

      const refreshCallsBefore = fetchSpy.mock.calls.filter(
        (c) => typeof c[0] === "string" && (c[0] as string).endsWith("/api/devices"),
      ).length;

      await user.click(screen.getByRole("button", { name: /refresh/i }));

      await waitFor(() => {
        const after = fetchSpy.mock.calls.filter(
          (c) => typeof c[0] === "string" && (c[0] as string).endsWith("/api/devices"),
        ).length;
        expect(after).toBeGreaterThan(refreshCallsBefore);
      });
    });

    it("renders only one Refresh button in the panel", async () => {
      renderPanel();
      await waitForDevices();
      expect(screen.getAllByRole("button", { name: /refresh/i })).toHaveLength(1);
    });
  });
});
