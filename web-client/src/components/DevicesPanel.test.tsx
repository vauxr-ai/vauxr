import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DevicesPanel from "./DevicesPanel";

const announce = vi.fn(), command = vi.fn();
const api = { announce, command };
vi.mock("../hooks/useHttpApi", () => ({ deriveHttpUrl: () => "http://localhost", useHttpApi: () => api }));
let devices: any[], speech: any, calls: {url:string; body:any}[];
let fail = "";
function response(body: unknown, status = 200) { return {ok:status === 200, status, json:async () => body} as Response; }
beforeEach(() => {
  vi.clearAllMocks(); fail = ""; calls = [];
  devices = [{id:"d1",name:"Living Room",state:"idle",lastSeen:"2026-01-01T12:00:00Z",config:{name:"Living Room",follow_up_mode:"auto",voice_mode:"webrtc"}}, {id:"d2",name:"Kitchen",state:"offline",lastSeen:"2026-01-01T12:00:00Z",config:{}}];
  speech = {defaults:{mode:"standard",stt_backend:"whisper",tts_backend:"piper",voices:{piper:"amy"}},overrides:{},effective:{stt_backend:"whisper",tts_backend:"piper",voice_id:"amy"},voice:{mode:"standard"},backends:[{id:"whisper",kind:"stt",model:"base",voices:[],readiness:"ready"},{id:"piper",kind:"tts",model:"en",voices:["amy"],readiness:"ready"}],policy:{assigned:false,agent_id:"nova",fallback_agent:"Nova",agents:[{id:"nova",name:"Nova"},{id:"ada",name:"Ada"}],available_modes:["standard"]}};
  globalThis.fetch = vi.fn(async (url, init) => {
    const path = String(url), body = init?.body ? JSON.parse(String(init.body)) : undefined;
    if (body) calls.push({url:path,body});
    if (fail && path.includes(fail)) return response({error:"Cannot save now"},503);
    if (path.endsWith("/speech")) {
      if (body?.agent_id !== undefined) { speech.policy.assigned = !!body.agent_id; speech.policy.agent_id = body.agent_id || "nova"; }
      if (body && !("agent_id" in body)) speech.overrides = body.reset ? {} : {...speech.overrides,...body};
      return response(structuredClone(speech));
    }
    if (path.includes("/metrics")) return response({window:"24h",bucket_seconds:3600,buckets:[],totals:{},metrics:[],observed_since:"2026-01-01T00:00:00Z",retention_days:30});
    if (path.includes("/api/webhooks")) return response([{id:"w1",name:"Lights"}]);
    if (path.endsWith("/api/devices")) return response(structuredClone(devices));
    if (body) { const d = devices.find(d => path.endsWith(d.id)); d.config = {...d.config,...body}; if (body.name) d.name=body.name; if (body.transport_mode) d.config.voice_mode=body.transport_mode; return response(structuredClone(d)); }
    return response({});
  }) as typeof fetch;
});
afterEach(() => vi.restoreAllMocks());
function panel(extra = {}) { return render(<DevicesPanel wsUrl="ws://localhost" token="" wsState="connected" addLog={vi.fn()} {...extra}/>); }
async function open() { await userEvent.click(await screen.findByRole("button",{name:/Living Room.*idle/})); await screen.findByRole("combobox",{name:"Assigned agent"}); }
it("shows disconnected and empty states", async () => {
  const view=panel({wsState:"disconnected"}); expect(screen.getByText(/Connect to a server/)).toBeVisible(); view.unmount(); devices=[]; panel(); expect(await screen.findByText("No devices yet")).toBeVisible();
});
it("selects complete rows, opens one device at a time and keeps administration independent of voice", async () => {
  panel(); await open(); expect(screen.getByRole("heading",{name:"Device features"})).toBeVisible(); await userEvent.click(screen.getByRole("button",{name:/Kitchen.*offline/})); expect(screen.getAllByRole("region",{name:"Assigned agent"})).toHaveLength(1);
});
it("autosaves agent assignment, names the default, and hides overrides until requested", async () => {
  panel(); await open(); expect(screen.getByRole("option",{name:"Use default agent (Nova)"})).toBeVisible(); expect(screen.queryByRole("combobox",{name:"STT backend"})).toBeNull(); expect(screen.queryByRole("button",{name:"Save agent assignment"})).toBeNull();
  await userEvent.selectOptions(screen.getByRole("combobox",{name:"Assigned agent"}),"ada"); await waitFor(() => expect(calls).toContainEqual({url:"/api/devices/d1/speech",body:{agent_id:"ada"}})); expect(await screen.findByText("Saved")).toBeVisible();
  await userEvent.click(screen.getByRole("button",{name:"Override agent voice pipeline defaults"})); expect(screen.getByRole("combobox",{name:"Voice transport"})).toBeVisible(); expect(screen.getByRole("combobox",{name:"TTS backend"})).toBeVisible();
  await userEvent.selectOptions(screen.getByRole("combobox",{name:"Voice transport"}),"websocket"); await waitFor(() => expect(calls.some(c=>c.body.transport_mode==="websocket")).toBe(true));
});
it("preserves saved selection and presents errors on rejected autosaves", async () => {
  panel(); await open(); fail="/speech"; await userEvent.selectOptions(screen.getByRole("combobox",{name:"Assigned agent"}),"ada"); expect(await screen.findByRole("alert")).toHaveTextContent("Cannot save now"); expect(screen.getByRole("combobox",{name:"Assigned agent"})).toHaveValue("");
});
it("renames inline on Enter and leaves the pencil next to title", async () => {
  panel(); await open(); await userEvent.click(screen.getByRole("button",{name:"Edit device name"})); const input=screen.getByRole("textbox",{name:"Device name"}); await userEvent.clear(input); await userEvent.type(input,"Studio{Enter}"); await waitFor(() => expect(calls.some(c=>c.body.name==="Studio")).toBe(true)); expect(await screen.findByRole("heading",{name:"Studio"})).toContainElement(screen.getByRole("button",{name:"Edit device name"}));
});
it("autosaves follow-up segmented choice and barge-in switch", async () => {
  panel(); await open(); await userEvent.click(screen.getByRole("button",{name:"Always",exact:true})); await waitFor(() => expect(screen.getByRole("button",{name:"Always"})).toHaveAttribute("aria-pressed","true"));
  await userEvent.click(screen.getByRole("switch",{name:"Barge-in",exact:true})); await waitFor(() => expect(calls.some(c=>c.body.barge_in===false)).toBe(true));
});
it.each(["websocket","realtime"])("does not offer unsupported barge-in for %s", async mode => { devices[0].config.voice_mode=mode; if (mode==="realtime") speech.voice.mode="realtime"; panel(); await open(); expect(screen.queryByRole("switch",{name:"Barge-in",exact:true})).toBeNull(); });
it("preserves webhook gesture mappings after save", async () => {
  panel(); await open(); await userEvent.selectOptions(screen.getByRole("combobox",{name:"Double press action"}),"webhook"); await waitFor(() => expect(calls.some(c=>c.body.button_actions?.double_press?.webhook_id==="w1")).toBe(true)); expect(screen.getByRole("combobox",{name:"Double press webhook"})).toHaveValue("w1");
});
it("uses the requested announcement default, retains edited text, and targets the correct device", async () => {
  panel(); await open(); const input=screen.getByRole("textbox",{name:"Announcement message"}); expect(input).toHaveValue("Hello world! this is the vauxr speaker test announcement. I sure hope you can hear me");
  await userEvent.clear(input); await userEvent.type(input,"Testing the kitchen"); await userEvent.click(screen.getByRole("button",{name:"Test Announce"})); expect(announce).toHaveBeenCalledWith("d1","Testing the kitchen"); expect(input).toHaveValue("Testing the kitchen");
});
it("sends slider volume on release rather than every input change", async () => {
  panel(); await open(); const range=screen.getByRole("slider",{name:"Output volume"}); fireEvent.change(range,{target:{value:"72"}}); expect(command).not.toHaveBeenCalled(); fireEvent.pointerUp(range); await waitFor(() => expect(command).toHaveBeenCalledWith("d1","set_volume",{volume:72}));
});
it("exposes all diagnostics simultaneously in a separate modal and sends validated controls", async () => {
  panel(); await open(); expect(screen.queryByRole("button",{name:"Reboot device"})).toBeNull(); await userEvent.click(screen.getByRole("button",{name:"Device Diagnostics"})); const dialog=screen.getByRole("dialog",{name:"Device Diagnostics"});
  for (const name of ["Set volume","Mute","Unmute","Reboot device","Install firmware","Set barge-in"]) expect(within(dialog).getByRole("button",{name,exact:true})).toBeVisible();
  expect(within(dialog).queryByRole("combobox",{name:"Command",exact:true})).toBeNull(); await userEvent.click(within(dialog).getByRole("button",{name:"Mute",exact:true})); await waitFor(()=>expect(command).toHaveBeenCalledWith("d1","mute",undefined));
  await userEvent.type(within(dialog).getByRole("textbox",{name:"Firmware URL"}),"https://example.test/speaker.bin"); await userEvent.click(within(dialog).getByRole("button",{name:"Install firmware"})); await waitFor(()=>expect(command).toHaveBeenCalledWith("d1","ota",{url:"https://example.test/speaker.bin"}));
  await userEvent.click(within(dialog).getByRole("button",{name:"Close dialog"})); expect(screen.queryByRole("dialog")).toBeNull(); expect(screen.getByRole("button",{name:"Device Diagnostics"})).toHaveFocus();
});
it("scopes session logs by exact device ID", async () => {
  panel({logs:[{id:1,time:new Date(),dir:"sys",text:"Only d1",deviceId:"d1"},{id:2,time:new Date(),dir:"sys",text:"Misleading d1 mention",deviceId:"d2"}]}); await open(); await userEvent.click(screen.getByText("Session activity log")); expect(screen.getByText("Only d1")).toBeVisible(); expect(screen.queryByText("Misleading d1 mention")).toBeNull();
});
it("shows refresh failures and retries", async () => { fail="/api/devices"; panel(); expect(await screen.findByRole("alert")).toHaveTextContent("Cannot save now"); fail=""; await userEvent.click(screen.getByRole("button",{name:"Refresh",exact:true})); await screen.findByRole("button",{name:/Living Room.*idle/}); });
it("cancels an inline name draft without saving", async () => {
 panel(); await open(); await userEvent.click(screen.getByRole("button",{name:"Edit device name"})); const input=screen.getByRole("textbox",{name:"Device name"}); await userEvent.clear(input); await userEvent.type(input,"Cancel this{Escape}"); expect(calls.filter(c=>c.body.name)).toHaveLength(0); expect(screen.getByRole("heading",{name:"Living Room"})).toBeVisible();
});

it("refreshes open voice overrides after companion mode changes without saving configuration", async () => {
  panel(); await open();
  await userEvent.click(screen.getByRole("button", {name:"Override agent voice pipeline defaults"}));
  expect(screen.getByRole("combobox", {name:"TTS backend"})).toBeVisible();
  speech.voice = {mode:"realtime", realtime_backend:"live", realtime_voice:"cedar"};
  speech.policy.available_modes = ["standard", "realtime"];
  speech.backends.push({id:"live", kind:"realtime", model:"live", voices:["cedar"], readiness:"ready"});
  await act(async () => { window.dispatchEvent(new CustomEvent("speech-settings-changed", {detail:{source:"talk",deviceId:"d1"}})); });
  expect(await screen.findByRole("combobox", {name:"Realtime voice"})).toBeVisible();
  expect(screen.queryByRole("combobox", {name:"TTS backend"})).toBeNull();
  expect(calls).toHaveLength(0);
});
