"""Run with python directly against production aiortc dependencies."""
import asyncio
import unittest
from types import SimpleNamespace

from aiortc import AudioStreamTrack, RTCConfiguration, RTCPeerConnection
from aiortc.rtp import RtpPacket
from realtime_rtp_diagnostics import install, monitor


class ReceiverBoundaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_actual_srtp_decoder_track_and_cleanup(self):
        sender = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        target = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        sender.addTrack(AudioStreamTrack())
        probes = []
        watch = None
        try:
            await sender.setLocalDescription(await sender.createOffer())
            await target.setRemoteDescription(sender.localDescription)
            receiver = target.getReceivers()[0]
            connection = SimpleNamespace(pc=target)
            original_recv = receiver.track.recv
            probes = install(connection)
            self.assertEqual(len(probes), 1)
            self.assertEqual(install(connection), [])
            await target.setLocalDescription(await target.createAnswer())
            await sender.setRemoteDescription(target.localDescription)
            for _ in range(12):
                frame = await asyncio.wait_for(receiver.track.recv(), 5)
                self.assertEqual(frame.sample_rate, 48000)
                self.assertEqual(frame.samples, 960)
            await asyncio.sleep(0)
            row = probes[0].drain()
            self.assertGreaterEqual(row['packets'], 16)
            self.assertGreaterEqual(row['assembled_frames'], 12)
            self.assertGreaterEqual(row['decoded_frames'], 12)
            self.assertEqual(row['delivered_frames'], 12)
            self.assertEqual(row['timestamp_delta_min'], 960)
            self.assertEqual(row['timestamp_delta_max'], 960)
            self.assertEqual(row['sequence_delta_max'], 1)
            self.assertTrue(row['decoder_alive'])
            # Unknown PT is rejected by the real receiver before jitter admission.
            before = dict(probes[0].counts)
            await receiver._handle_rtp_packet(RtpPacket(payload_type=127,
                sequence_number=1000, timestamp=9999, ssrc=123, payload=b'\xf8\xff\xfe'), 0)
            self.assertEqual(probes[0].counts, before)
            # Diagnostic counter failure cannot interrupt actual audio delivery.
            original_count = probes[0].count
            def fail(*args):
                raise RuntimeError('synthetic diagnostic failure')
            probes[0].count = fail
            self.assertEqual((await asyncio.wait_for(receiver.track.recv(), 3)).samples, 960)
            probes[0].count = original_count
            watch = asyncio.create_task(monitor(connection, 'test', probes))
            await asyncio.sleep(0)
            watch.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await watch
            self.assertEqual(receiver.track.recv, original_recv)
            self.assertFalse(hasattr(receiver, '_vauxr_probe'))
            # Restoring instrumentation leaves normal media consumption healthy.
            self.assertEqual((await asyncio.wait_for(receiver.track.recv(), 3)).samples, 960)
            print('actual SRTP -> jitter -> Opus decoder -> track:', row)
        finally:
            if watch is not None and not watch.done():
                watch.cancel()
                await asyncio.gather(watch, return_exceptions=True)
            for probe in probes:
                probe.restore()
            await sender.close()
            await target.close()


if __name__ == '__main__':
    unittest.main()
