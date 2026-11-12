import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TelemetrySettings from "./TelemetrySettings";

const initial = { enabled: false, acknowledged: false, environment_disabled: false,
  endpoint_ready: false, payload: '{"schema_version":1,"monthly_id":"actual-wire-id"}',
  payload_status: "proposed startup", queued: 0 };
function response(body: unknown, ok = true) {
  return { ok, status: ok ? 200 : 503, json: async () => body } as Response;
}
afterEach(() => vi.restoreAllMocks());
it("shows notice and exact preview before any enable request, with a real opt-out", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(response(initial));
  const user = userEvent.setup();
  render(<TelemetrySettings />);
  expect(await screen.findByText(/Awaiting your choice/)).toBeInTheDocument();
  expect(screen.getByText(initial.payload)).toBeInTheDocument();
  expect(screen.getByText(/AWS sees the transport IP/)).toBeInTheDocument();
  expect(screen.getByText(/not provider latency/)).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch.mock.calls[0][1]?.method).toBeUndefined();
  fetch.mockResolvedValue(response({ ...initial, acknowledged: true }));
  await user.click(screen.getByRole("button", { name: "Keep telemetry off" }));
  await waitFor(() => expect(fetch.mock.calls.at(-1)?.[1]?.body).toBe(
    JSON.stringify({ enabled: false, acknowledged: true })));
  expect(await screen.findByText(/Telemetry disabled/)).toBeInTheDocument();
});
it("respects environment override and retains refresh after uncertain save", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(response({ ...initial,
    environment_disabled: true, payload: null }));
  render(<TelemetrySettings />);
  expect(await screen.findByText(/Disabled by server environment/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Enable telemetry" })).not.toBeInTheDocument();
});
it("shows save failure without claiming success", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(response(initial));
  const user = userEvent.setup();
  render(<TelemetrySettings />);
  await screen.findByText(/Awaiting your choice/);
  fetch.mockResolvedValue(response({}, false));
  await user.click(screen.getByRole("button", { name: "Enable telemetry" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Choice could not be confirmed");
  expect(screen.getByRole("button", { name: "Refresh status and payload" })).toBeEnabled();
});
