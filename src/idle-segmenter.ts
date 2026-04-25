export interface IdleSegmenterOptions {
  idlePauseMs: number;
  onSegment: (text: string) => void;
  onEnd: (finalSegment: string | null) => void;
}

/**
 * Buffers incoming delta text and flushes it as a segment whenever the
 * stream goes idle for `idlePauseMs`. Pure timer logic — no I/O.
 */
export class IdleSegmenter {
  private buffer = "";
  private timer: ReturnType<typeof setTimeout> | null = null;
  private ended = false;

  constructor(private opts: IdleSegmenterOptions) {}

  push(text: string): void {
    if (this.ended || text.length === 0) return;
    this.buffer += text;
    this.armTimer();
  }

  end(): void {
    if (this.ended) return;
    this.ended = true;
    this.clearTimer();
    if (this.buffer.length > 0) {
      const final = this.buffer;
      this.buffer = "";
      this.opts.onEnd(final);
    } else {
      this.opts.onEnd(null);
    }
  }

  abort(): void {
    if (this.ended) return;
    this.ended = true;
    this.clearTimer();
    this.buffer = "";
  }

  private armTimer(): void {
    this.clearTimer();
    this.timer = setTimeout(() => {
      this.timer = null;
      this.flush();
    }, this.opts.idlePauseMs);
  }

  private clearTimer(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }

  private flush(): void {
    if (this.ended) return;
    if (this.buffer.length === 0) return;
    const segment = this.buffer;
    this.buffer = "";
    this.opts.onSegment(segment);
  }
}
