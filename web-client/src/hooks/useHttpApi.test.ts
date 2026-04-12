import { renderHook } from "@testing-library/react";
import { deriveHttpUrl, useHttpApi } from "./useHttpApi";

describe("deriveHttpUrl", () => {
  it("converts ws:// to http:// with default port", () => {
    expect(deriveHttpUrl("ws://192.168.1.10:8765")).toBe("http://192.168.1.10:8080");
  });

  it("converts wss:// to https:// with default port", () => {
    expect(deriveHttpUrl("wss://example.com:8765")).toBe("https://example.com:8080");
  });

  it("uses custom httpPort when provided", () => {
    expect(deriveHttpUrl("ws://host:8765", 9000)).toBe("http://host:9000");
  });

  it("returns empty string for empty input instead of throwing", () => {
    expect(() => deriveHttpUrl("")).not.toThrow();
    expect(deriveHttpUrl("")).toBe("");
  });

  it("returns empty string for malformed URL instead of throwing", () => {
    expect(() => deriveHttpUrl("not a url")).not.toThrow();
    expect(deriveHttpUrl("not a url")).toBe("");
  });
});

describe("useHttpApi", () => {
  const BASE_URL = "http://localhost:8080";
  const TOKEN = "test-token-123";

  let mockFetch: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    mockFetch = vi.fn();
    vi.stubGlobal("fetch", mockFetch);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function setup() {
    const { result } = renderHook(() => useHttpApi(BASE_URL, TOKEN));
    return result;
  }

  describe("listDevices", () => {
    it("calls GET /api/devices with correct Authorization header", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ devices: [] }),
      });

      const result = setup();
      await result.current.listDevices();

      expect(mockFetch).toHaveBeenCalledWith(
        `${BASE_URL}/api/devices`,
        expect.objectContaining({
          headers: expect.objectContaining({
            Authorization: `Bearer ${TOKEN}`,
          }),
        }),
      );
    });

    it("returns parsed device array on 200", async () => {
      const devices = [
        { id: "d1", name: "Device 1", state: "idle", lastSeen: "2026-01-01" },
      ];
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ devices }),
      });

      const result = setup();
      const list = await result.current.listDevices();
      expect(list).toEqual(devices);
    });

    it("throws with error message from response body on 404", async () => {
      mockFetch.mockResolvedValue({
        ok: false,
        status: 404,
        statusText: "Not Found",
        json: () => Promise.resolve({ error: "No devices found" }),
      });

      const result = setup();
      await expect(result.current.listDevices()).rejects.toThrow("No devices found");
    });

    it("throws with error message from response body on 401", async () => {
      mockFetch.mockResolvedValue({
        ok: false,
        status: 401,
        statusText: "Unauthorized",
        json: () => Promise.resolve({ error: "Invalid token" }),
      });

      const result = setup();
      await expect(result.current.listDevices()).rejects.toThrow("Invalid token");
    });
  });

  describe("announce", () => {
    it("calls POST /api/devices/{id}/announce with correct body", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({}),
      });

      const result = setup();
      await result.current.announce("dev-1", "Hello world");

      expect(mockFetch).toHaveBeenCalledWith(
        `${BASE_URL}/api/devices/dev-1/announce`,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ text: "Hello world" }),
        }),
      );
    });

    it("resolves on 200", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({}),
      });

      const result = setup();
      await expect(result.current.announce("dev-1", "Hello")).resolves.toBeUndefined();
    });

    it("throws with server error message on non-2xx", async () => {
      mockFetch.mockResolvedValue({
        ok: false,
        status: 500,
        statusText: "Internal Server Error",
        json: () => Promise.resolve({ error: "TTS failed" }),
      });

      const result = setup();
      await expect(result.current.announce("dev-1", "Hello")).rejects.toThrow("TTS failed");
    });
  });

  describe("command", () => {
    it("calls POST /api/devices/{id}/command with body { command: 'mute' } (no params)", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({}),
      });

      const result = setup();
      await result.current.command("dev-1", "mute");

      expect(mockFetch).toHaveBeenCalledWith(
        `${BASE_URL}/api/devices/dev-1/command`,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ command: "mute" }),
        }),
      );
    });

    it("calls with body { command: 'set_volume', volume: 75 } when params provided", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({}),
      });

      const result = setup();
      await result.current.command("dev-1", "set_volume", { volume: 75 });

      expect(mockFetch).toHaveBeenCalledWith(
        `${BASE_URL}/api/devices/dev-1/command`,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ command: "set_volume", volume: 75 }),
        }),
      );
    });

    it("resolves on 200", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({}),
      });

      const result = setup();
      await expect(result.current.command("dev-1", "mute")).resolves.toBeUndefined();
    });

    it("throws with server error message on non-2xx", async () => {
      mockFetch.mockResolvedValue({
        ok: false,
        status: 500,
        statusText: "Internal Server Error",
        json: () => Promise.resolve({ error: "Device offline" }),
      });

      const result = setup();
      await expect(result.current.command("dev-1", "mute")).rejects.toThrow("Device offline");
    });
  });
});
