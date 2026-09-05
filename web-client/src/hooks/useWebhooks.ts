import { useCallback, useRef } from "react";

export interface ApiWebhook {
  id: string;
  name: string;
  url: string;
  has_authorization: boolean;
  body?: Record<string, unknown> | null;
}

export function useWebhooks(baseUrl: string, token: string) {
  const baseUrlRef = useRef(baseUrl);
  const tokenRef = useRef(token);
  baseUrlRef.current = baseUrl;
  tokenRef.current = token;

  const request = useCallback(async (path: string, init?: RequestInit): Promise<Response> => {
    const res = await fetch(`${baseUrlRef.current}${path}`, {
      ...init,
      headers: {
        ...init?.headers,
        Authorization: `Bearer ${tokenRef.current}`,
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

  const listWebhooks = useCallback(async (): Promise<ApiWebhook[]> => {
    const res = await request("/api/webhooks");
    return await res.json();
  }, [request]);

  const createWebhook = useCallback(
    async (
      name: string,
      url: string,
      authorization?: string,
      body?: Record<string, unknown>,
    ): Promise<ApiWebhook> => {
      const res = await request("/api/webhooks", {
        method: "POST",
        body: JSON.stringify({
          name,
          url,
          authorization: authorization || undefined,
          body: body ?? undefined,
        }),
      });
      return await res.json();
    },
    [request],
  );

  const updateWebhook = useCallback(
    async (
      id: string,
      patch: {
        name?: string;
        url?: string;
        authorization?: string;
        body?: Record<string, unknown> | null;
      },
    ): Promise<ApiWebhook> => {
      const res = await request(`/api/webhooks/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      });
      return await res.json();
    },
    [request],
  );

  const deleteWebhook = useCallback(async (id: string): Promise<void> => {
    await request(`/api/webhooks/${encodeURIComponent(id)}`, { method: "DELETE" });
  }, [request]);

  return { listWebhooks, createWebhook, updateWebhook, deleteWebhook };
}
