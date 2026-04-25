import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { loadDeviceConfigs } from "../src/device-config.js";

let dataDir: string;
let warnSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  dataDir = mkdtempSync(join(tmpdir(), "vauxr-device-config-"));
  warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  rmSync(dataDir, { recursive: true, force: true });
  warnSpy.mockRestore();
});

function writeConfig(contents: string): void {
  writeFileSync(join(dataDir, "devices.json"), contents);
}

describe("loadDeviceConfigs", () => {
  it("returns {} when file is missing", () => {
    expect(loadDeviceConfigs(dataDir)).toEqual({});
    expect(warnSpy).not.toHaveBeenCalled();
  });

  it("returns {} and logs a warning on invalid JSON", () => {
    writeConfig("{ not json");
    expect(loadDeviceConfigs(dataDir)).toEqual({});
    expect(warnSpy).toHaveBeenCalled();
  });

  it("returns parsed config on a valid file", () => {
    writeConfig(JSON.stringify({
      "dev-a": { name: "Living Room", voice: true, follow_up_mode: "always" },
      "dev-b": { follow_up_mode: "never" },
    }));
    expect(loadDeviceConfigs(dataDir)).toEqual({
      "dev-a": { name: "Living Room", voice: true, follow_up_mode: "always" },
      "dev-b": { follow_up_mode: "never" },
    });
  });

  it("ignores unknown fields silently", () => {
    writeConfig(JSON.stringify({
      "dev-a": { name: "Bedroom", color: "red", extra: 42 },
    }));
    expect(loadDeviceConfigs(dataDir)).toEqual({
      "dev-a": { name: "Bedroom" },
    });
    expect(warnSpy).not.toHaveBeenCalled();
  });

  it("returns {} when looking up a device id that's not in the config", () => {
    writeConfig(JSON.stringify({
      "dev-a": { name: "Living Room" },
    }));
    const configs = loadDeviceConfigs(dataDir);
    expect(configs["dev-unknown"]).toBeUndefined();
    expect(configs["dev-unknown"] ?? {}).toEqual({});
  });

  it("warns and treats invalid follow_up_mode as 'auto'", () => {
    writeConfig(JSON.stringify({
      "dev-a": { follow_up_mode: "sometimes" },
    }));
    const configs = loadDeviceConfigs(dataDir);
    expect(configs["dev-a"]).toEqual({ follow_up_mode: "auto" });
    expect(warnSpy).toHaveBeenCalled();
  });

  it("returns {} when the top-level JSON is not an object", () => {
    writeConfig(JSON.stringify(["dev-a", "dev-b"]));
    expect(loadDeviceConfigs(dataDir)).toEqual({});
    expect(warnSpy).toHaveBeenCalled();
  });
});
