import { ownerFetch } from "../auth/api";
import { useCallback } from "react";

export interface ApiDevice {
  id: string;
  name: string;
  state: string;
  lastSeen: string;
}

export function deriveHttpUrl(wsUrl: string, httpPort = 8080): string {
  if (!wsUrl) return "";
  let url: URL;
  try {
    url = new URL(wsUrl);
  } catch {
    return "";
  }
  const scheme = url.protocol === "wss:" ? "https:" : "http:";
  return `${scheme}//${url.hostname}:${httpPort}`;
}

export function useHttpApi(_baseUrl = "", _token = "") {
  const request = useCallback(async (path: string, init?: RequestInit): Promise<Response> => {
    const res = await ownerFetch(path, {
      ...init,
      headers: {
        ...init?.headers,
        "Content-Type": "application/json",
      },
    });
    if (!res.ok) {
      let message = res.statusText;
      try {
        const body = await res.json();
        if (body.error) message = body.error;
        else if (body.message) message = body.message;
      } catch { /* use statusText */ }
      throw new Error(message);
    }
    return res;
  }, []);

  const listDevices = useCallback(async (): Promise<ApiDevice[]> => {
    const res = await request("/api/devices");
    const body = await res.json();
    return body.devices ?? body;
  }, [request]);

  const announce = useCallback(async (deviceId: string, text: string): Promise<void> => {
    await request(`/api/devices/${encodeURIComponent(deviceId)}/announce`, {
      method: "POST",
      body: JSON.stringify({ text }),
    });
  }, [request]);

  const command = useCallback(
    async (deviceId: string, cmd: string, params?: Record<string, unknown>): Promise<void> => {
      await request(`/api/devices/${encodeURIComponent(deviceId)}/command`, {
        method: "POST",
        body: JSON.stringify({ command: cmd, params }),
      });
    },
    [request],
  );

  return { listDevices, announce, command };
}
