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
          path.endsWith("channels")
            ? [{ id: "channel-1", name: "OpenClaw" }]
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
    subject: "channel-1",
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
    screen.queryByLabelText("Spoken eight-digit code"),
  ).not.toBeInTheDocument();
});
