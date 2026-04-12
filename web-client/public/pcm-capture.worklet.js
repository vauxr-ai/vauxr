/** AudioWorkletProcessor that buffers input and emits fixed-size chunks. */
class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.chunkSize = options?.processorOptions?.chunkSize ?? 1600;
    this.buffer = new Float32Array(this.chunkSize);
    this.offset = 0;
  }

  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;

    let srcOffset = 0;
    while (srcOffset < input.length) {
      const remaining = this.chunkSize - this.offset;
      const toCopy = Math.min(remaining, input.length - srcOffset);
      this.buffer.set(input.subarray(srcOffset, srcOffset + toCopy), this.offset);
      this.offset += toCopy;
      srcOffset += toCopy;

      if (this.offset === this.chunkSize) {
        this.port.postMessage(this.buffer.slice());
        this.offset = 0;
      }
    }
    return true;
  }
}

registerProcessor("pcm-capture", PcmCaptureProcessor);
