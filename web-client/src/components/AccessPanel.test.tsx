import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AccessPanel from "./AccessPanel";
import { ownerPost, ownerFetch } from "../auth/api";
vi.mock("../auth/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../auth/api")>()),
  ownerPost: vi.fn(),
  ownerFetch: vi.fn(),
}));
beforeEach(() => {
  vi.clearAllMocks();
  const values = new Map<string, string>();
  vi.stubGlobal("sessionStorage", {
    getItem: (k: string) => values.get(k) ?? null,
    setItem: (k: string, v: string) => values.set(k, v),
  });
  vi.stubGlobal(
    "confirm",
    vi.fn(() => true),
  );
  vi.mocked(ownerFetch).mockImplementation(
    async (path) =>
      new Response(
        JSON.stringify(
          path.endsWith("agents")
            ? [{ id: "agent-1", name: "OpenClaw" }]
            : [],
        ),
      ),
  );
});
it("retains integration revoke ID before a possibly committed error and retries that same operation", async () => {
  const operations: object[] = [];
  let attempts = 0;
  vi.mocked(ownerPost).mockImplementation(async (path, body = {}) => {
    if (path.endsWith("/list")) return { requests: [] };
    if (path.endsWith("/revoke")) {
      operations.push(body);
      expect(
        JSON.parse(sessionStorage.getItem("vauxr-operations")!)[0].operation_id,
      ).toBe((body as { operation_id: string }).operation_id);
      if (attempts++ === 0)
        throw new Error(
          "Request failed (503). Refresh status before retrying.",
        );
      return { ...body, action: "revoke", state: "revoked" };
    }
    throw new Error("Unexpected endpoint");
  });
  const user = userEvent.setup();
  render(<AccessPanel />);
  await user.click(await screen.findByText("revoke integration"));
  await screen.findByRole("alert");
  expect(screen.getByText(/outcome unknown/)).toBeInTheDocument();
  await user.click(screen.getByText("Retry same operation"));
  await screen.findByText(
    "Access revoked. Re-pair required; configuration retained.",
  );
  expect(operations).toHaveLength(2);
  expect(operations[0]).toEqual(operations[1]);
  expect(operations[0]).toMatchObject({
    role: "integration",
    subject: "agent-1",
  });
});
it("shows expired pairing as terminal and never infers consent from its name", async () => {
  vi.mocked(ownerPost).mockResolvedValue({
    requests: [
      {
        request_id: "r",
        device_id: "d",
        kind: "physical",
        display_name: "Speaker",
        status: "ready",
        expires_at: 1,
      },
    ],
  });
  render(<AccessPanel />);
  await waitFor(() =>
    expect(screen.getByText(/Request r · expired/)).toBeInTheDocument(),
  );
  expect(
    screen.queryByText("Initiate matching speaker"),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByLabelText("Spoken four-digit code"),
  ).not.toBeInTheDocument();
});

it("accepts the code once and initiates then approves on one deliberate click", async () => {
  const calls: string[] = [];
  vi.mocked(ownerPost).mockImplementation(async (path, body = {}) => {
    if (path.endsWith("/list")) return { requests: [{ request_id: "r", device_id: "d",
      display_name: "Speaker", kind: "physical", status: calls.length ? "approved" : "ready",
      expires_at: Date.now() / 1000 + 300 }] };
    calls.push(path);
    expect(body).toEqual({ request_id: "r", code: "0012" });
    return {};
  });
  const user = userEvent.setup();
  render(<AccessPanel />);
  await user.type(await screen.findByLabelText("Spoken four-digit code"), "0012");
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByText("Approve matching speaker"));
  await waitFor(() => expect(calls).toEqual([
    "/api/enrollment/v1/initiate", "/api/enrollment/v1/approve",
  ]));
  await waitFor(() => expect(screen.queryByLabelText("Spoken four-digit code")).not.toBeInTheDocument());
});

it("does not approve after initiation fails or retain the entered code", async () => {
  vi.mocked(ownerPost).mockImplementation(async (path) => {
    if (path.endsWith("/list")) return { requests: [{ request_id: "r", device_id: "d",
      display_name: "Speaker", kind: "physical", status: "ready", expires_at: Date.now() / 1000 + 300 }] };
    throw new Error("Pairing request expired");
  });
  const user = userEvent.setup();
  render(<AccessPanel />);
  await user.type(await screen.findByLabelText("Spoken four-digit code"), "0012");
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByText("Approve matching speaker"));
  await screen.findByText("Pairing request expired");
  expect(screen.getByLabelText("Spoken four-digit code")).toHaveValue("");
  expect(vi.mocked(ownerPost).mock.calls.filter(([path]) => path.endsWith("/approve"))).toHaveLength(0);
});

it.each(["approval", "initiation response", "approval and refresh"])(
  "reconciles pairing status and retries after a failed %s",
  async (failure) => {
    let status = "ready";
    let failed = false;
    let refreshFailed = false;
    const calls: string[] = [];
    vi.mocked(ownerPost).mockImplementation(async (path, body = {}) => {
      if (path.endsWith("/list")) {
        if (failure === "approval and refresh" && failed && !refreshFailed) {
          refreshFailed = true;
          throw new Error("Status unavailable");
        }
        return { requests: [{ request_id: "r", device_id: "d",
          display_name: "Speaker", kind: "physical", status,
          expires_at: Date.now() / 1000 + 300 }] };
      }
      calls.push(path);
      expect(body).toEqual({ request_id: "r", code: "0012" });
      if (path.endsWith("/initiate")) {
        if (status !== "ready") throw new Error("Already initiated");
        status = "initiated";
        if (failure === "initiation response" && !failed) {
          failed = true;
          throw new Error("Pairing temporarily unavailable");
        }
      } else if (path.endsWith("/approve")) {
        if (!failed) {
          failed = true;
          throw new Error("Pairing temporarily unavailable");
        }
        status = "approved";
      } else throw new Error("Unexpected endpoint");
      return {};
    });
    const user = userEvent.setup();
    render(<AccessPanel />);
    async function approve() {
      await user.type(await screen.findByLabelText("Spoken four-digit code"), "0012");
      await user.click(screen.getByRole("checkbox"));
      await user.click(screen.getByText("Approve matching speaker"));
    }
    await approve();
    expect(await screen.findByRole("alert")).toHaveTextContent("Pairing temporarily unavailable");
    expect(screen.getByLabelText("Spoken four-digit code")).toHaveValue("");
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    await approve();
    await waitFor(() => expect(screen.queryByLabelText("Spoken four-digit code")).not.toBeInTheDocument());
    expect(calls.filter((path) => path.endsWith("/initiate"))).toHaveLength(1);
    expect(status).toBe("approved");
  },
);

it("loads and saves configurable pairing messages", async () => {
  vi.mocked(ownerPost).mockImplementation(async (path, body = {}) => {
    if (path.endsWith("/list")) return { requests: [] };
    if (path.endsWith("/prompts")) return { intro: "Welcome.", code: "Your code is {code}." };
    if (path.endsWith("/save-prompts")) return body;
    throw new Error("Unexpected endpoint");
  });
  const user = userEvent.setup();
  render(<AccessPanel />);
  await user.click(screen.getByText("Edit pairing messages"));
  const intro = await screen.findByLabelText("Welcome message");
  await user.clear(intro);
  await user.type(intro, "Welcome to Vauxr! Press Action when ready.");
  await user.click(screen.getByText("Save pairing messages"));
  await screen.findByRole("status");
  expect(ownerPost).toHaveBeenCalledWith("/api/enrollment/v1/save-prompts", {
    intro: "Welcome to Vauxr! Press Action when ready.", code: "Your code is {code}.",
  });
});
