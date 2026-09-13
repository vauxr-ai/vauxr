import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import OwnerGate from "./OwnerGate";
import { ownerFetch, setCsrf } from "./api";

vi.mock("./browser", () => ({ retireBrowser: vi.fn(async () => {}) }));

it.each(["broadcast", "expiry", "login"])("ignores delayed success after %s logout/expiry", async (mode) => {
  setCsrf("");
  let deliver: (() => void) | undefined;
  vi.stubGlobal("BroadcastChannel", class {
    set onmessage(callback: () => void) { deliver = callback; }
    close() {}
  });
  let release!: (response: Response) => void;
  const delayed = new Promise<Response>((resolve) => { release = resolve; });
  const fetcher = vi.fn(async (path: string) => {
    if (path.endsWith("status")) return Response.json({ state: "generated" });
    if (path.endsWith("session")) return mode === "login" ? Response.json({}, { status: 401 }) : delayed;
    if (path.endsWith("login")) return delayed;
    return Response.json({});
  });
  vi.stubGlobal("fetch", fetcher);
  render(<OwnerGate><main>Administration</main></OwnerGate>);
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith("/api/auth/session", expect.anything()));
  if (mode === "login") {
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Operator token or console code"), "test-token");
    await user.click(screen.getByText("Log in with saved operator token"));
  }
  act(() => {
    if (mode === "expiry") window.dispatchEvent(new Event("owner-expired"));
    else deliver!();
  });
  await act(async () => { release(Response.json({ csrf_token: "stale" })); await delayed; });
  expect(screen.queryByRole("main")).not.toBeInTheDocument();
  await ownerFetch("/api/devices");
  const init = fetcher.mock.calls.at(-1) as unknown as [string, RequestInit];
  expect(new Headers(init[1].headers).has("X-CSRF-Token")).toBe(false);
});
