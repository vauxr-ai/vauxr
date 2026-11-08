import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import CompanionControls from "./CompanionControls";
import { setCsrf } from "../auth/api";

const agents = [{ id: "agent", name: "Household" }, { id: "other", name: "Workshop" }];
const listAgents = vi.fn(async () => agents);
vi.mock("../hooks/useAgents", () => ({ useAgents: () => ({ listAgents }) }));
const credential = () => "browser-private";
function setup() {
  let revision = 0;
  const fetcher = vi.fn(async (path: string, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body || "{}"));
    const response = path.endsWith("/speech") ? { policy: { agent_id: "agent" } }
      : path.endsWith("/context") ? { ...body, revision: `revision-${++revision}` }
      : path.endsWith("/propose") ? { proposal_id: "proposal", revision: body.revision,
          resource_id: "agent", old_value: "Household", new_value: body.new_value }
      : {};
    return new Response(JSON.stringify(response), { status: 200 });
  });
  vi.stubGlobal("fetch", fetcher); setCsrf("csrf");
  return fetcher;
}
const props = { browserId: "B1", credential, epoch: 1, route: "agents",
  selection: { id: "agent", name: "Household" }, onSwitch: vi.fn() };

afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });

test("exact owner proposal preview, confirmation and navigation invalidation", async () => {
  const fetcher = setup(); const user = userEvent.setup();
  const mounted = render(<CompanionControls {...props} />);
  await waitFor(() => expect(screen.getByLabelText("Proposed display name")).toBeEnabled());
  await user.type(screen.getByLabelText("Proposed display name"), "Kitchen");
  await user.click(screen.getByText("Review name change"));
  await screen.findByRole("group", { name: "Confirm display name change" });
  expect(screen.getByText("Household", { selector: "strong" })).toBeVisible();
  expect(screen.getByText("Kitchen", { selector: "strong" })).toBeVisible();
  expect(fetcher.mock.calls.filter(([path]) => path.endsWith("/confirm"))).toHaveLength(0);
  mounted.rerender(<CompanionControls {...props} route="settings" selection={null} />);
  expect(screen.queryByRole("group")).not.toBeInTheDocument();
  mounted.rerender(<CompanionControls {...props} />);
  await waitFor(() => expect(screen.getByLabelText("Proposed display name")).toBeEnabled());
  await user.type(screen.getByLabelText("Proposed display name"), "Kitchen");
  await user.click(screen.getByText("Review name change"));
  await user.click(await screen.findByRole("button", { name: "Confirm display name change" }));
  await waitFor(() => expect(fetcher.mock.calls.some(([path]) => path.endsWith("/confirm"))).toBe(true));
  for (const [path, init] of fetcher.mock.calls.filter(([path]) => path.includes("/companion/"))) {
    const headers = new Headers(init?.headers);
    expect(headers.get("X-Vauxr-Browser")).toBe("browser-private");
    expect(headers.get("X-CSRF-Token")).toBe("csrf");
    expect(String(init?.body)).not.toContain("browser-private");
    expect(path).not.toContain("?");
  }
});

test("switch affects only proven browser and reconnect republishes context without mic access", async () => {
  const fetcher = setup(); const user = userEvent.setup();
  const getUserMedia = vi.fn(); vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
  const mounted = render(<CompanionControls {...props} />);
  await screen.findByRole("option", { name: "Workshop" });
  await user.selectOptions(screen.getByLabelText("Conversation agent"), "other");
  await waitFor(() => expect(props.onSwitch).toHaveBeenCalledTimes(1));
  const selection = fetcher.mock.calls.find(([path]) => path.endsWith("/select"));
  expect(JSON.parse(String(selection?.[1]?.body))).toEqual({ browser_id: "B1", agent_id: "other" });
  expect(fetcher.mock.calls.some(([path]) => path.includes("activate"))).toBe(false);
  const before = fetcher.mock.calls.filter(([path]) => path.endsWith("/context")).length;
  mounted.rerender(<CompanionControls {...props} epoch={2} />);
  await waitFor(() => expect(fetcher.mock.calls.filter(([path]) => path.endsWith("/context")).length).toBeGreaterThan(before));
  expect(getUserMedia).not.toHaveBeenCalled();
});

test("sends real text operation with browser proof, shows response and never requests voice", async () => {
  const fetcher = setup();
  fetcher.mockImplementation(async (path: string, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body || "{}"));
    return new Response(JSON.stringify(path.endsWith("/chat")
      ? { request_id: body.request_id, text: "Text-only response" }
      : path.endsWith("/speech") ? { policy: { agent_id: "agent" } } : { revision: "r" }), { status: 200 });
  });
  const user = userEvent.setup();
  const getUserMedia = vi.fn();
  vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
  render(<CompanionControls {...props} />);
  await user.type(screen.getByLabelText("Text message"), "Explain the name");
  await user.click(screen.getByRole("button", { name: "Send text" }));
  expect(await screen.findByLabelText("Agent text response")).toHaveTextContent("Text-only response");
  const call = fetcher.mock.calls.find(([path]) => path.endsWith("/chat"))!;
  expect(JSON.parse(String(call[1]?.body))).toEqual({ browser_id: "B1", request_id: expect.stringMatching(/^[a-f0-9]{32}$/), text: "Explain the name" });
  expect(new Headers(call[1]?.headers).get("X-Vauxr-Browser")).toBe("browser-private");
  expect(getUserMedia).not.toHaveBeenCalled();
  expect(props.onSwitch).not.toHaveBeenCalled();
  expect(screen.getByText(/You enter this proposal; integration-generated UI proposals are not connected/)).toBeVisible();
});
