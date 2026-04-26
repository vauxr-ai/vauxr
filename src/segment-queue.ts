export interface SegmentQueueOptions {
  synthesize: (text: string) => Promise<void>;
  signal: AbortSignal;
  onError?: (err: Error) => void;
}

/**
 * Async FIFO of text segments with a single serial worker that calls the
 * provided `synthesize` callback for each segment in order. Used by the
 * pipeline to keep TTS playback strictly ordered while segments are being
 * produced upstream by the idle-segmenter.
 */
export class SegmentQueue {
  private items: string[] = [];
  private closed = false;
  private wakeup: (() => void) | null = null;
  private workerPromise: Promise<void>;

  constructor(private opts: SegmentQueueOptions) {
    this.workerPromise = this.run();
  }

  push(segment: string): void {
    if (this.closed || segment.length === 0) return;
    this.items.push(segment);
    this.notify();
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.notify();
  }

  /** Resolves when the worker has drained the queue (or aborted). */
  done(): Promise<void> {
    return this.workerPromise;
  }

  private notify(): void {
    if (this.wakeup) {
      const w = this.wakeup;
      this.wakeup = null;
      w();
    }
  }

  private async run(): Promise<void> {
    while (true) {
      if (this.opts.signal.aborted) return;

      if (this.items.length === 0) {
        if (this.closed) return;
        await new Promise<void>((resolve) => {
          this.wakeup = resolve;
        });
        continue;
      }

      const segment = this.items.shift()!;
      const pending = this.items.length;
      console.log(`[segment-queue] synthesizing (${segment.length} chars, ${pending} queued): "${segment.substring(0, 80)}${segment.length > 80 ? "..." : ""}"`);
      try {
        await this.opts.synthesize(segment);
        console.log(`[segment-queue] done (${segment.length} chars)`);
      } catch (err) {
        if (this.opts.onError) {
          this.opts.onError(err as Error);
        } else {
          console.error("[segment-queue] synth error:", (err as Error).message);
        }
      }
    }
  }
}
