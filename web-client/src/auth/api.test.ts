import { clientPost, ownerFetch, setCsrf } from "./api";
import { transcript } from "./browser";
import fixture from "../../../tests/fixtures/enrollment-v1-vector.json";
it("matches the frozen enrollment transcript byte for byte", () => {
  expect(transcript(fixture.action, fixture.challenge)).toBe(
    fixture.message_ascii,
  );
});
it("keeps cookie and bearer authority separate, blocks redirects and cross-origin API paths", async () => {
  const fetcher = vi.fn(async () => new Response("{}"));
  vi.stubGlobal("fetch", fetcher);
  setCsrf("memory-csrf");
  await ownerFetch("/api/devices", {
    method: "PATCH",
    headers: { Authorization: "Bearer forbidden" },
  });
  const owner = fetcher.mock.calls[0] as unknown as [string, RequestInit];
  expect(owner[1].credentials).toBe("same-origin");
  expect(new Headers(owner[1].headers).has("Authorization")).toBe(false);
  expect(new Headers(owner[1].headers).get("X-CSRF-Token")).toBe("memory-csrf");
  await clientPost("ack", "scoped-device", {
    operation_id: "public-id",
    saved: true,
  });
  const client = fetcher.mock.calls[1] as unknown as [string, RequestInit];
  expect(client[1].credentials).toBe("omit");
  expect(client[1].redirect).toBe("error");
  expect(new Headers(client[1].headers).has("X-CSRF-Token")).toBe(false);
  await expect(
    ownerFetch("https://other.example/api/devices"),
  ).rejects.toThrow();
});
