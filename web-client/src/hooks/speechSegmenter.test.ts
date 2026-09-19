import { SpeechSegmenter } from "./speechSegmenter";

it("bounds pre-roll, rejects clicks and endpoints successive utterances while capture stays open", () => {
  const detector = new SpeechSegmenter();
  const start = vi.fn(), send = vi.fn(), end = vi.fn();
  const silence = new Int16Array(1600), speech = new Int16Array(1600).fill(2000);
  const push = (pcm: Int16Array) => detector.push(pcm, start, send, end);
  for (let i = 0; i < 200; i++) push(silence);
  push(speech); push(silence);
  expect(start).not.toHaveBeenCalled();
  push(speech); push(speech);
  expect(start).toHaveBeenCalledTimes(1);
  expect(send).toHaveBeenCalledTimes(3);
  for (let i = 0; i < 7; i++) push(silence);
  expect(end).toHaveBeenCalledTimes(1);
  push(speech); push(speech);
  expect(start).toHaveBeenCalledTimes(2);
  detector.reset();
  push(silence);
  expect(detector.active).toBe(false);
});

it("caps uninterrupted speech at 30 seconds", () => {
  const detector = new SpeechSegmenter();
  const end = vi.fn();
  for (let i = 0; i < 302; i++) detector.push(new Int16Array(1600).fill(2000), vi.fn(), vi.fn(), end);
  expect(end).toHaveBeenCalledTimes(1);
});
