/** 16 kHz PCM endpointing. Memory is bounded to 300 ms before speech.
 * Two voiced chunks reject brief clicks; 700 ms silence ends a turn.
 * Capture stays open, including while the response plays (browser AEC enabled).
 */
export class SpeechSegmenter {
  private pre: Int16Array[] = [];
  private voiced = 0;
  private quiet = 0;
  private duration = 0;
  active = false;
  reset() { this.pre = []; this.voiced = this.quiet = this.duration = 0; this.active = false; }
  push(pcm: Int16Array, start: () => void, send: (pcm: Int16Array) => void, end: () => void) {
    const samples = pcm.length;
    const rms = Math.sqrt(pcm.reduce((sum, v) => sum + (v / 32768) ** 2, 0) / samples);
    const speech = rms >= 0.018;
    if (!this.active) {
      this.pre.push(pcm.slice());
      while (this.pre.reduce((n, p) => n + p.length, 0) > 4800) this.pre.shift();
      this.voiced = speech ? this.voiced + samples : 0;
      if (this.voiced < 3200) return;
      this.active = true;
      this.quiet = this.duration = 0;
      start();
      this.pre.forEach(send);
      this.pre = [];
      return;
    }
    send(pcm);
    this.duration += samples;
    this.quiet = speech ? 0 : this.quiet + samples;
    if (this.quiet >= 11200 || this.duration >= 16000 * 30) {
      this.reset();
      end();
    }
  }
}
