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
});
