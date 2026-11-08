import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AgentsPanel from "./AgentsPanel";
const mocks = vi.hoisted(() => ({
  listAgents: vi.fn(),
  renameAgent: vi.fn(),
  activateAgent: vi.fn(),
}));
vi.mock("../hooks/useAgents", () => ({ useAgents: () => mocks }));
vi.mock("./SpeechSettings", () => ({ default: () => null }));
vi.mock("./IntegrationEnrollment", () => ({ default: () => null }));

it("keeps the name draft after a failed save and only claims success after persistence", async () => {
  const agent = {
    id: "stable-id",
    name: "Original",
    active: true,
    type: "openclaw",
  };
  mocks.listAgents.mockResolvedValue([agent]);
  mocks.renameAgent.mockRejectedValueOnce(
    new Error("Unable to save. Refresh before retrying."),
  );
  const onContext = vi.fn();
  render(<AgentsPanel onContext={onContext} />);
  await userEvent.click(
    await screen.findByRole("heading", { name: "Original" }),
  );

  expect(screen.getByRole("button", { name: "Default", exact: true })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Activate", exact: true })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", {name:"Edit agent name"}));
  const input = screen.getByLabelText("Agent name");
  await userEvent.clear(input);
  await userEvent.type(input, "New name");
  await userEvent.click(screen.getByRole("button", { name: "Save name" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Unable to save");
  expect(input).toHaveValue("New name");
  expect(screen.queryByText("Agent name saved.")).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog")).toBeNull();
  mocks.renameAgent.mockResolvedValue({ id: "stable-id", name: "New name" });
  await userEvent.click(screen.getByRole("button", { name: "Save name" }));
  await waitFor(() =>
    expect(screen.getByRole("status")).toHaveTextContent("Agent name saved."),
  );
  expect(mocks.renameAgent).toHaveBeenLastCalledWith("stable-id", "New name");
  expect(onContext).toHaveBeenLastCalledWith("New name");
  expect(
    screen.getByRole("heading", { name: "New name" }),
  ).toBeInTheDocument();
});
