// Owner authority is exclusively the same-origin HttpOnly cookie. CSRF stays in memory.
let csrf = "";
export function setCsrf(value: string) {
  csrf = value;
}
export async function ownerFetch(path: string, init: RequestInit = {}) {
  if (!path.startsWith("/api/") || path.includes("?") || path.includes("#"))
    throw new Error("Invalid API path");
  const headers = new Headers(init.headers);
  headers.delete("Authorization");
  headers.set("Content-Type", "application/json");
  if (csrf) headers.set("X-CSRF-Token", csrf);
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "same-origin",
    cache: "no-store",
    redirect: "error",
    referrerPolicy: "no-referrer",
  });
  if (response.status === 401) window.dispatchEvent(new Event("owner-expired"));
  return response;
}
export async function jsonResponse(response: Response) {
  if (!response.ok) {
    // Do not reflect response bodies, submitted identifiers, or credentials into logs/UI.
    throw new Error(
      `Request failed (${response.status}). ${response.status === 429 ? "Wait one minute before retrying." : "Refresh status before retrying a change; its outcome may be uncertain."}`,
    );
  }
  return response.json();
}
export async function ownerPost(path: string, body: object = {}) {
  return jsonResponse(
    await ownerFetch(path, { method: "POST", body: JSON.stringify(body) }),
  );
}
export async function clientPost(
  action: string,
  token: string,
  body: object = {},
) {
  return jsonResponse(
    await fetch(`/api/lifecycle/v1/${action}`, {
      method: "POST",
      credentials: "omit",
      cache: "no-store",
      redirect: "error",
      referrerPolicy: "no-referrer",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(body),
    }),
  );
}
export function randomId() {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) =>
    b.toString(16).padStart(2, "0"),
  ).join("");
}
