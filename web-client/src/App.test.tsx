import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { setCsrf } from "./auth/api";
vi.mock("./auth/browser", () => ({ retireBrowser: vi.fn(async () => {}) }));
function storage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => {
      values.delete(key);
    },
    setItem: (key, value) => {
      values.set(key, String(value));
    },
  };
}
beforeEach(() => {
  setCsrf("");
  for (const name of ["sessionStorage", "localStorage"]) {
    const values = new Map<string, string>();
    vi.stubGlobal(name, {
      getItem: (k: string) => values.get(k) ?? null,
      setItem: (k: string, v: string) => values.set(k, v),
      removeItem: (k: string) => values.delete(k),
      clear: () => values.clear(),
      get length() {
        return values.size;
      },
    });
  }
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  Element.prototype.scrollIntoView = vi.fn();
});
function response(body: object, status = 200) {
  return new Response(JSON.stringify(body), { status });
}
function mockServer() {
  let authenticated = false;
  const fetcher = vi.fn(async (path: string, init?: RequestInit) => {
    if (path === "/api/auth/status")
      return response({ state: "unclaimed", environment_managed: false });
    if (path === "/api/auth/session")
      return response(
        authenticated ? { csrf_token: "csrf" } : {},
        authenticated ? 200 : 401,
      );
    if (path === "/api/auth/claim")
      return response({
        operator_token: "generated-only-once",
        save_acknowledgement: "ack",
      });
    if (path === "/api/auth/save") return response({ state: "generated" });
    if (path === "/api/auth/login") {
      authenticated = true;
      return response({ csrf_token: "csrf" });
    }
    if (path === "/api/auth/logout") {
      authenticated = false;
      return response({ logged_out: true });
    }
    if (path === "/api/enrollment/v1/list") return response({ requests: [] });
    if (init?.method === "POST") return response({});
    return response([]);
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}
it("denies administration without an owner session and requires deliberate save before login", async () => {
  const fetcher = mockServer();
  const user = userEvent.setup();
  render(<App />);
  await screen.findByText("Owner state: unclaimed");
  expect(screen.queryByRole("main")).not.toBeInTheDocument();
  await user.type(
    screen.getByLabelText("Operator token or console code"),
    "console-code",
  );
  await user.click(screen.getByText("Submit console setup/recovery code"));
  await screen.findByText("generated-only-once");
  expect(fetcher.mock.calls.some(([p]) => p === "/api/auth/save")).toBe(false);
  expect(localStorage.length).toBe(0);
  expect(sessionStorage.length).toBe(0);
  await user.click(screen.getByText("I saved this in my password manager"));
  await waitFor(() =>
    expect(screen.queryByText("generated-only-once")).not.toBeInTheDocument(),
  );
  expect(screen.queryByRole("main")).not.toBeInTheDocument();
  await user.type(
    screen.getByLabelText("Operator token or console code"),
    "generated-only-once",
  );
  await user.click(screen.getByText("Log in with saved operator token"));
  await screen.findByRole("main");
  expect(screen.getByText("Browser voice connection")).toBeInTheDocument();
  const listing = fetcher.mock.calls.find(
    ([p]) => p === "/api/enrollment/v1/list",
  )!;
  expect(new Headers(listing[1]?.headers).get("Authorization")).toBeNull();
  expect(new Headers(listing[1]?.headers).get("X-CSRF-Token")).toBe("csrf");
  await user.click(screen.getByText("Log out on this browser"));
  await waitFor(() =>
    expect(screen.queryByRole("main")).not.toBeInTheDocument(),
  );
});
it("shows environment-managed status and no generated setup action", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) =>
      path.endsWith("status")
        ? response({ state: "environment", environment_managed: true })
        : response({}, 401),
    ),
  );
  render(<App />);
  await screen.findByText(/Environment-managed owner access/);
  expect(
    screen.queryByText("Submit console setup/recovery code"),
  ).not.toBeInTheDocument();
});
