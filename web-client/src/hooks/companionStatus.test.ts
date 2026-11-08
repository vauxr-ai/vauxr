import { companionStatus } from "./companionStatus";
const ready = {
  connected: true,
  capturing: false,
  enrolling: false,
  state: "connected" as const,
  realtime: "stopped" as const,
  outputMuted: false,
};
it("distinguishes pending connection, failures and output mute without claiming the microphone is off during capture", () => {
  expect(
    companionStatus({ ...ready, connected: false, state: "connecting" }),
  ).toBe("Connecting · Mic off");
  expect(companionStatus({ ...ready, connected: false, enrolling: true })).toBe(
    "Connecting · Mic off",
  );
  expect(
    companionStatus({ ...ready, state: "connecting", error: "failure" }),
  ).toBe("Connection failed · Mic off");
  expect(companionStatus({ ...ready, capturing: true, error: "failure" })).toBe(
    "Connection failed · Mic active",
  );
  expect(
    companionStatus({ ...ready, connected: false, capturing: true }),
  ).not.toContain("Mic off");
  expect(
    companionStatus({
      ...ready,
      capturing: true,
      state: "speaking",
      outputMuted: true,
    }),
  ).toBe("Speaking · Output muted");
  expect(
    companionStatus({ ...ready, capturing: true, state: "processing" }),
  ).toBe("Thinking");
  expect(companionStatus({ ...ready, capturing: true })).toBe("Listening");
});
