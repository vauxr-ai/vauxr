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

it("offers keyboard-accessible guide help without submitting or clearing the draft", async () => {
  const user = userEvent.setup();
  const onCreated = vi.fn();
  const fetcher = vi.fn(async () => ({ok:true,json:async()=>({definitions:[{id:"custom",name:"Custom",framework:"langchain",supported_modes:["standard"]}]})}));
  vi.stubGlobal("fetch", fetcher);
  render(<AddAgent onCreated={onCreated}/>);
  await user.click(screen.getByRole("button",{name:"+ Add Agent"}));
  await user.type(await screen.findByLabelText("Agent name"), "Draft agent");
  const help = screen.getByRole("link",{name:"How to create a custom agent (opens in a new tab)"});
  expect(help).toHaveAttribute("href","https://github.com/vauxr-ai/vauxr/blob/docs/maintainer-review-20261109/docs/how-to-create-custom-agent.md");
  expect(help).toHaveAttribute("target","_blank");
  expect(help).toHaveAttribute("rel","noopener noreferrer");
  help.focus();
  expect(help).toHaveFocus();
  await user.keyboard("{Enter}");
  expect(screen.getByLabelText("Agent name")).toHaveValue("Draft agent");
  expect(onCreated).not.toHaveBeenCalled();
  expect(fetcher.mock.calls).toHaveLength(1);
});
