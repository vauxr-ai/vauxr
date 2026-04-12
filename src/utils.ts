export function makeBinaryFrame(type: number, seq: number, payload: Buffer): Buffer {
  const header = Buffer.alloc(3);
  header[0] = type;
  header.writeUInt16BE(seq, 1);
  return Buffer.concat([header, payload]);
}
