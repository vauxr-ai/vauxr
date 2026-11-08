import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AddAgent from "./AddAgent";
it("creates an instance from the actual registered definition ID", async () => {
  const user = userEvent.setup(),
    onCreated = vi.fn();
  const fetcher = vi.fn(async (_path: string, init?: RequestInit) => ({
    ok: true,
    json: async () =>
      init?.method === "POST"
        ? { id: "new-instance", name: "Kitchen" }
        : {
            definitions: [
              {
                id: "household",
                name: "Household",
                framework: "langchain",
                supported_modes: ["standard"],
              },
            ],
          },
  }));
  vi.stubGlobal("fetch", fetcher);
  render(<AddAgent onCreated={onCreated} />);
  await user.click(screen.getByRole("button", { name: "+ Add Agent" }));
  await screen.findByRole("option", { name: "Household · langchain" });
  await user.type(screen.getByLabelText("Agent name"), "Kitchen");
  await user.click(screen.getByRole("button", { name: "Create agent" }));
  expect(onCreated).toHaveBeenCalledWith({
    id: "new-instance",
    name: "Kitchen",
  });
  expect(
    JSON.parse(
      fetcher.mock.calls.find((c) => c[1]?.method === "POST")![1]!
        .body as string,
    ),
  ).toEqual({ definition_id: "household", name: "Kitchen" });
});
