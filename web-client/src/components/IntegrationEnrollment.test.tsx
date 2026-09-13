import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import IntegrationEnrollment from "./IntegrationEnrollment";
import { ownerPost } from "../auth/api";
vi.mock("../auth/api", () => ({ ownerPost: vi.fn() }));
const request = { request_id: "a".repeat(32), channel_id: "int_test", origin: "http://localhost",
  server_id: "b".repeat(32), display_name: "Test client", state: "pending", expires_at: Date.now() / 1000 + 300 };
beforeEach(() => {
  vi.mocked(ownerPost).mockReset();
  vi.mocked(ownerPost).mockResolvedValue({ requests: [request] });
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
