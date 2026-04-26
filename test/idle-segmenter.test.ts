import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { IdleSegmenter } from "../src/idle-segmenter.js";

describe("IdleSegmenter", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("does not flush while deltas keep arriving inside the idle window", () => {
    const segments: string[] = [];
    const ends: Array<string | null> = [];
    const seg = new IdleSegmenter({
      idlePauseMs: 400,
      onSegment: (s) => segments.push(s),
      onEnd: (s) => ends.push(s),
    });

    seg.push("the ");
    vi.advanceTimersByTime(100);
    seg.push("quick ");
    vi.advanceTimersByTime(100);
    seg.push("brown ");
    vi.advanceTimersByTime(100);
    seg.push("fox");

    expect(segments).toEqual([]);
    expect(ends).toEqual([]);
  });

  it("flushes after an idle gap >= idlePauseMs", () => {
    const segments: string[] = [];
    const seg = new IdleSegmenter({
      idlePauseMs: 400,
      onSegment: (s) => segments.push(s),
      onEnd: () => {},
    });

    seg.push("hello world");
    vi.advanceTimersByTime(399);
    expect(segments).toEqual([]);
    vi.advanceTimersByTime(1);
    expect(segments).toEqual(["hello world"]);
  });

  it("emits multiple flushes within one run when there are multiple gaps", () => {
    const segments: string[] = [];
    const seg = new IdleSegmenter({
      idlePauseMs: 400,
      onSegment: (s) => segments.push(s),
      onEnd: () => {},
    });

    seg.push("first chunk");
    vi.advanceTimersByTime(500);
    expect(segments).toEqual(["first chunk"]);

    seg.push("second chunk");
    vi.advanceTimersByTime(500);
    expect(segments).toEqual(["first chunk", "second chunk"]);

    seg.push("third chunk");
    vi.advanceTimersByTime(500);
    expect(segments).toEqual(["first chunk", "second chunk", "third chunk"]);
  });

  it("end() flushes leftover buffer as the final segment", () => {
    const segments: string[] = [];
    const ends: Array<string | null> = [];
    const seg = new IdleSegmenter({
      idlePauseMs: 400,
      onSegment: (s) => segments.push(s),
      onEnd: (s) => ends.push(s),
    });

    seg.push("partial buffer");
    vi.advanceTimersByTime(100);
    seg.end();

    expect(segments).toEqual([]);
    expect(ends).toEqual(["partial buffer"]);
  });

  it("end() with empty buffer emits onEnd(null)", () => {
    const segments: string[] = [];
    const ends: Array<string | null> = [];
    const seg = new IdleSegmenter({
      idlePauseMs: 400,
      onSegment: (s) => segments.push(s),
      onEnd: (s) => ends.push(s),
    });

    seg.push("flushed");
    vi.advanceTimersByTime(500);
    expect(segments).toEqual(["flushed"]);

    seg.end();
    expect(ends).toEqual([null]);
  });

  it("abort() cancels the timer and drops the buffer without firing callbacks", () => {
    const segments: string[] = [];
    const ends: Array<string | null> = [];
    const seg = new IdleSegmenter({
      idlePauseMs: 400,
      onSegment: (s) => segments.push(s),
      onEnd: (s) => ends.push(s),
    });

    seg.push("doomed buffer");
    vi.advanceTimersByTime(100);
    seg.abort();
    vi.advanceTimersByTime(1000);

    expect(segments).toEqual([]);
    expect(ends).toEqual([]);
  });

  it("ignores pushes after end()", () => {
    const segments: string[] = [];
    const seg = new IdleSegmenter({
      idlePauseMs: 400,
      onSegment: (s) => segments.push(s),
      onEnd: () => {},
    });

    seg.end();
    seg.push("late");
    vi.advanceTimersByTime(1000);

    expect(segments).toEqual([]);
  });

  it("a delta arriving after a flush starts a new buffer that arms the timer again", () => {
    const segments: string[] = [];
    const seg = new IdleSegmenter({
      idlePauseMs: 400,
      onSegment: (s) => segments.push(s),
      onEnd: () => {},
    });

    seg.push("first");
    vi.advanceTimersByTime(500);
    expect(segments).toEqual(["first"]);

    seg.push("second");
    vi.advanceTimersByTime(200);
    expect(segments).toEqual(["first"]);
    vi.advanceTimersByTime(300);
    expect(segments).toEqual(["first", "second"]);
  });
});
