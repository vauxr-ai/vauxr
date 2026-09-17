import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import IntegrationEnrollment from "./IntegrationEnrollment";
import { ownerPost } from "../auth/api";
vi.mock("../auth/api", () => ({ ownerPost: vi.fn() }));
const request = { request_id: "a".repeat(32), agent_id: "int_test", origin: "http://localhost",
  server_id: "b".repeat(32), display_name: "Test client", state: "pending", expires_at: 1800000300 };
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(1800000000000);
  vi.mocked(ownerPost).mockReset();
  vi.mocked(ownerPost).mockResolvedValue({ requests: [request] });
});
afterEach(() => vi.useRealTimers());
test("discloses fixed grants, exclusions and physical pairing limits immediately before consent", async () => {
  render(<IntegrationEnrollment />);
  fireEvent.click(screen.getByText("Refresh integration requests"));
  const row = await screen.findByRole("region", { name: `Integration request ${request.request_id}` });
  const disclosure = within(row).getByRole("region", { name: "Integration permissions" });
  expect(disclosure).toBeVisible();
  for (const text of [
    /fixed permissions after it acknowledges durable credential save, even while its agent is inactive/,
    /List devices, send device announcements, and issue allowed device controls/,
    /Initiate firmware updates, including OTA/,
    /Initiate and approve physical device pairing/,
    /Each pairing approval still requires a fresh verified physical pairing window/,
    /confirmation of the matching code spoken by the device/,
    /Connect to its own agent and respond to existing voice requests on that agent when active/,
    /Agent activation selects voice routing; it does not grant these permissions or gate device access/,
    /Device playback is reserved; no playback URL endpoint is available/,
    /does not grant owner administration/,
    /credential creation, disclosure, rotation or revocation/,
    /device configuration, button mappings or speech\/provider settings/,
    /agent listing or configuration; webhook configuration; server management/,
    /firmware image reading, upload or publication; or device impersonation/,
    /cannot approve browser enrollment or known-device recovery/,
    /choose a replacement device key, or receive a device credential/,
  ]) expect(disclosure).toHaveTextContent(text);
  const consent = within(row).getByRole("checkbox");
  expect(disclosure.nextElementSibling).toBe(consent.closest("label"));
  expect(consent).not.toBeChecked();
  expect(within(row).getByRole("button", { name: "Approve matching integration" })).toBeDisabled();
  expect(ownerPost).toHaveBeenCalledTimes(1);
});
test("approval requires matching code and deliberate confirmation, and clears code after uncertain failure", async () => {
  render(<IntegrationEnrollment />);
  fireEvent.click(screen.getByText("Refresh integration requests"));
  const input = await screen.findByLabelText("Integration matching code");
  const approve = screen.getByText("Approve matching integration");
  fireEvent.change(input, { target: { value: "abcdef12" } });
  expect(approve).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox"));
  expect(approve).toBeEnabled();
  vi.mocked(ownerPost).mockRejectedValueOnce(new Error("Request failed. Refresh before retrying."));
  fireEvent.click(approve);
  await screen.findByRole("alert");
  expect(ownerPost).toHaveBeenLastCalledWith("/api/integrations/v1/approve", {
    request_id: request.request_id, user_code: "ABCDEF12",
  });
  expect(input).toHaveValue("");
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(approve).toBeDisabled();
});
test("delivered state never claims save completion or reoffers approval", async () => {
  vi.mocked(ownerPost).mockResolvedValue({ requests: [{ ...request, state: "delivered" }] });
  render(<IntegrationEnrollment />);
  fireEvent.click(screen.getByText("Refresh integration requests"));
  await screen.findByText("Delivery attempted; durable save has NOT been acknowledged.");
  expect(screen.queryByText("Approve matching integration")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Integration matching code")).not.toBeInTheDocument();
});
test("denial sends only request identity and requires confirmation", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<IntegrationEnrollment />);
  fireEvent.click(screen.getByText("Refresh integration requests"));
  const deny = await screen.findByText("Deny integration request");
  fireEvent.click(deny);
  expect(ownerPost).toHaveBeenCalledTimes(1);
  confirm.mockReturnValue(true);
  fireEvent.click(deny);
  await waitFor(() => expect(ownerPost).toHaveBeenCalledWith("/api/integrations/v1/deny", { request_id: request.request_id }));
  confirm.mockRestore();
});
