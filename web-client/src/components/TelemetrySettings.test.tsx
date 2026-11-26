import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TelemetrySettings from "./TelemetrySettings";

const initial = { enabled: true, environment_disabled: false,
  endpoint_ready: false, payload: '{"schema_version":1,"monthly_id":"actual-wire-id"}',
  payload_status: "queued", queued: 1 };
function response(body: unknown, ok = true) {
  return { ok, status: ok ? 200 : 503, json: async () => body } as Response;
}
afterEach(() => vi.restoreAllMocks());
it("shows reporting enabled without a write, with an exact preview and opt-out", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(response(initial));
  const user = userEvent.setup();
  render(<TelemetrySettings />);
  expect(await screen.findByText(/Telemetry enabled/)).toBeInTheDocument();
  expect(screen.getByText(initial.payload)).toBeInTheDocument();
  expect(screen.getByText(
    "No conversations, audio, prompts, responses, errors, logs, secrets, hostnames, URLs, device or user names, or location are sent.",
  )).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch.mock.calls[0][1]?.method).toBeUndefined();
  fetch.mockResolvedValue(response({ ...initial, enabled: false, payload: null }));
  await user.click(screen.getByRole("button", { name: "Disable telemetry" }));
  await waitFor(() => expect(fetch.mock.calls.at(-1)?.[1]?.body).toBe(
    JSON.stringify({ enabled: false, acknowledged: true })));
  expect(await screen.findByText(/Telemetry disabled/)).toBeInTheDocument();
});
it("respects environment override and retains refresh after uncertain save", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(response({ ...initial,
    environment_disabled: true, payload: null }));
  render(<TelemetrySettings />);
  expect(await screen.findByText(/Disabled by server environment/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Disable telemetry" })).not.toBeInTheDocument();
});
it("shows save failure without claiming success", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(response(initial));
  const user = userEvent.setup();
  render(<TelemetrySettings />);
  await screen.findByText(/Telemetry enabled/);
  fetch.mockResolvedValue(response({}, false));
  await user.click(screen.getByRole("button", { name: "Disable telemetry" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Choice could not be confirmed");
  expect(screen.getByRole("button", { name: "Refresh status and payload" })).toBeEnabled();
});
