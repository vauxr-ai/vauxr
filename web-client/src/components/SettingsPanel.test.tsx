import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SettingsPanel from "./SettingsPanel";

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    statusText: ok ? "OK" : "Error",
    json: async () => body,
  } as unknown as Response;
}

let fetchSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
  fetchSpy = vi.fn().mockResolvedValue(jsonResponse([]));
  globalThis.fetch = fetchSpy as unknown as typeof fetch;
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SettingsPanel", () => {
  it("asks to connect when disconnected", () => {
    render(
      <SettingsPanel wsUrl="" token="" wsState="disconnected" addLog={vi.fn()} />,
    );
    expect(screen.getByText(/connect to a server to manage webhooks/i)).toBeInTheDocument();
  });

  it("lists webhooks and can create one", async () => {
    const user = userEvent.setup();
    const created = {
      id: "wh_1",
      name: "HA",
      url: "http://ha.local/hook",
      has_authorization: true,
    };
    fetchSpy.mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === "POST") return Promise.resolve(jsonResponse(created, true, 201));
      if (String(url).includes("/api/webhooks")) {
        return Promise.resolve(jsonResponse([created]));
      }
      return Promise.resolve(jsonResponse([]));
    });

    render(
      <SettingsPanel
        wsUrl="ws://localhost:8765/ws"
        token="tok"
        wsState="connected"
        addLog={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Webhooks")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /add webhook/i }));
    await user.type(screen.getByPlaceholderText(/home assistant/i), "HA");
    await user.type(screen.getByPlaceholderText(/homeassistant\.local/i), "http://ha.local/hook");
    await user.click(screen.getByRole("button", { name: /^create$/i }));

    await waitFor(() => {
      const post = fetchSpy.mock.calls.find(
        (c) => (c[1] as RequestInit | undefined)?.method === "POST",
      );
      expect(post).toBeDefined();
      expect(JSON.parse((post![1] as RequestInit).body as string)).toEqual({
        name: "HA",
        url: "http://ha.local/hook",
      });
    });
  });

  it("duplicates a webhook via POST /api/webhooks/{id}/duplicate", async () => {
    const user = userEvent.setup();
    const existing = {
      id: "wh_1",
      name: "HA",
      url: "http://ha.local/hook",
      has_authorization: true,
    };
    const cloned = {
      id: "wh_2",
      name: "HA copy",
      url: "http://ha.local/hook",
      has_authorization: true,
    };
    fetchSpy.mockImplementation((url: string, init?: RequestInit) => {
      const path = String(url);
      if (init?.method === "POST" && path.includes("/duplicate")) {
        return Promise.resolve(jsonResponse(cloned, true, 201));
      }
      if (path.includes("/api/webhooks")) {
        const posted = fetchSpy.mock.calls.some(
          (c) =>
            String(c[0]).includes("/duplicate") &&
            (c[1] as RequestInit | undefined)?.method === "POST",
        );
        return Promise.resolve(jsonResponse(posted ? [existing, cloned] : [existing]));
      }
      return Promise.resolve(jsonResponse([]));
    });

    const addLog = vi.fn();
    render(
      <SettingsPanel
        wsUrl="ws://localhost:8765/ws"
        token="tok"
        wsState="connected"
        addLog={addLog}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("HA")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /^duplicate$/i }));

    await waitFor(() => {
      const post = fetchSpy.mock.calls.find(
        (c) =>
          String(c[0]).includes("/api/webhooks/wh_1/duplicate") &&
          (c[1] as RequestInit | undefined)?.method === "POST",
      );
      expect(post).toBeDefined();
      expect(addLog).toHaveBeenCalledWith("sys", "Webhook duplicated: HA copy");
    });
  });

  it("toggles authorization visibility on the add form", async () => {
    const user = userEvent.setup();
    render(
      <SettingsPanel
        wsUrl="ws://localhost:8765/ws"
        token="tok"
        wsState="connected"
        addLog={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Webhooks")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /add webhook/i }));
    const input = screen.getByPlaceholderText(/bearer eyj/i);
    expect(input).toHaveAttribute("type", "password");
    await user.click(screen.getByRole("button", { name: /show authorization/i }));
    expect(input).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", { name: /hide authorization/i })).toBeInTheDocument();
  });
});
