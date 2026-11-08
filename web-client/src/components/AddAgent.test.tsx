import { render, screen, waitFor } from "@testing-library/react";
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
  await user.click(await screen.findByRole("button", { name: "Choose Household" }));
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

it("shows processing, retains failed creation and closes only after success", async () => {
  const user=userEvent.setup();
  let finish: (response: unknown)=>void = ()=>{};
  const created=vi.fn();
  const fetcher=vi.fn((_path:string,init?:RequestInit)=>init?.method==="POST" ? new Promise(resolve=>{finish=resolve;}) : Promise.resolve({ok:true,json:async()=>({definitions:[{id:"vauxr-support",name:"Vauxr Support",framework:"langchain",supported_modes:["standard"]}]})}));
  vi.stubGlobal("fetch",fetcher);
  render(<AddAgent onCreated={created}/>);
  await user.click(screen.getByRole("button",{name:"+ Add Agent"}));
  await user.click(await screen.findByRole("button",{name:"Choose Vauxr Support"}));
  await user.type(screen.getByLabelText("Agent name"),"My Support");
  await user.click(screen.getByRole("button",{name:"Create agent"}));
  expect(screen.getByRole("status")).toHaveTextContent("Creating agent");
  expect(screen.getByRole("button",{name:"Creating agent…"})).toBeDisabled();
  finish({ok:false,status:503});
  expect(await screen.findByRole("alert")).toHaveTextContent("Request failed (503)");
  expect(screen.getByRole("dialog",{name:"Add Agent"})).toBeVisible();
  expect(screen.getByLabelText("Agent name")).toHaveValue("My Support");
  await user.click(screen.getByRole("button",{name:"Create agent"}));
  finish({ok:true,json:async()=>({id:"new",name:"My Support"})});
  await waitFor(()=>expect(screen.queryByRole("dialog")).toBeNull());
  expect(created).toHaveBeenCalledTimes(1);
});
