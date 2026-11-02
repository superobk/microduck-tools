import unittest
from unittest.mock import Mock
from servo_workbench.protocol import crc16, unstuff, packet
from servo_workbench.profiles import Refused, NoAck
from servo_workbench.transport import SerialBus


class Wire:
    def __init__(self, **kwargs):
        self.buffer = bytearray()
        self.regs = {0: 1200, 7: 1, 8: 3, 64: 0}
        self.written = []
        self.partial = False
        self.no_ack = False
        self.frames = []
        self.port = None
        self.baudrate = kwargs.get('baudrate')

    def open(self): pass
    def close(self): pass
    def fileno(self): return 99
    def reset_input_buffer(self): self.buffer.clear()
    def reset_output_buffer(self): pass

    def write(self, data):
        self.written.append(bytes(data))
        if self.partial:
            return 3
        dxl = data.startswith(b'\xff\xff\xfd\x00')
        ident = data[4] if dxl else data[2]
        body = unstuff(data[7:-2]) if dxl else data[4:-1]
        instruction = body[0]
        count = 2 if dxl else 1
        address = int.from_bytes(body[1:1 + count], 'little')
        if instruction == 2:
            size = int.from_bytes(body[1 + count:1 + 2 * count], 'little')
            result = self.regs.get(address, 0).to_bytes(size, 'little')
        else:
            self.regs[address] = int.from_bytes(body[1 + count:], 'little')
            if self.no_ack:
                return len(data)
            result = b''
        if dxl:
            p = b'\xff\xff\xfd\x00' + bytes([ident]) + (len(result) + 4).to_bytes(2, 'little') + bytes([0x55, 0]) + result
            p += crc16(p).to_bytes(2, 'little')
        else:
            p = bytes([ident, len(result) + 2, 0]) + result
            p = b'\xff\xff' + p + bytes([(~sum(p)) & 255])
        self.buffer.extend(p)
        return len(data)

    def read(self, n):
        result = bytes(self.buffer[:n])
        del self.buffer[:n]
        return result


class SerialTests(unittest.TestCase):
    def bus(self, kind='xl330', board=False, guard=None):
        row = dict(device='MOCK', vid=1, pid=2, serial_number='mock')
        self.wire = Wire()
        return SerialBus(kind, row, board=board, ports_fn=lambda: [row],
                         serial_factory=lambda **kwargs: self.wire, guard=guard)

    def test_native_read_uses_real_codec_fake_descriptor(self):
        bus = self.bus()
        self.assertEqual(bus.read(1, 1000000, 0, 2), 1200)
        self.assertEqual(self.wire.written[0], packet('dxl2', 1, 2, b'\x00\x00\x02\x00'))

    def test_feetech_read_and_write(self):
        bus = self.bus('hd1910')
        bus.write(1, 1000000, 55, 1, 0)
        self.assertEqual(bus.read(1, 1000000, 55, 1), 0)
        self.assertTrue(all(p.startswith(b'\xff\xff\x01') for p in self.wire.written))

    def test_partial_tx_closes_without_resend(self):
        bus = self.bus(); self.wire.partial = True
        with self.assertRaises(ConnectionError):
            bus.write(1, 1000000, 9, 1, 0)
        self.assertEqual(len(self.wire.written), 1)
        self.assertIsNone(bus.serial)

    def test_no_ack_is_typed_unknown_not_success(self):
        bus = self.bus(); self.wire.no_ack = True
        with self.assertRaises(NoAck):
            bus.write(1, 1000000, 9, 1, 0)
        self.assertEqual(bus.read(1, 1000000, 9, 1), 0)
        self.assertEqual(len([p for p in self.wire.written if p[7] == 3]), 1)

    def test_robotd_starting_blocks_before_tx(self):
        def guard(*args):
            raise Refused('ROBOTD_ACTIVE', 'robotd is starting')
        bus = self.bus(board=True, guard=guard)
        with self.assertRaises(Refused):
            bus.read(1, 1000000, 0, 2)
        self.assertEqual(self.wire.written, [])

    def test_owner_checked_again_after_open(self):
        guard = Mock()
        bus = self.bus(board=True, guard=guard)
        bus.read(1, 1000000, 0, 2)
        self.assertGreaterEqual(guard.call_count, 3)

    def test_disallowed_read_never_opens_port(self):
        bus = self.bus()
        with self.assertRaises(Refused):
            bus.read(1, 1000000, 12345, 2)
        self.assertIsNone(bus.serial)

    def test_dynamixel_official_sdk_packet_equivalence(self):
        import dynamixel_sdk
        class Capture:
            is_using = False
            sent = None
            def clearPort(self): pass
            def writePort(self, value): self.sent = bytes(value); return len(value)
            def setPacketTimeout(self, value): pass
        port = Capture()
        sdk = dynamixel_sdk.PacketHandler(2.0)
        sdk.readTx(port, 1, 132, 4)
        self.assertEqual(port.sent, packet('dxl2', 1, 2, b'\x84\x00\x04\x00'))
        port.is_using = False
        sdk.writeTxOnly(port, 1, 116, 4, [1, 2, 3, 4])
        self.assertEqual(port.sent, packet('dxl2', 1, 3, b'\x74\x00\x01\x02\x03\x04'))

    def test_sdk_returned_stuffing_and_crc_agree(self):
        # SDK 3.8.4 txPacket ignores addStuffing's newly allocated return value.
        # Compare the returned reference algorithm, not that lossy TX path.
        import dynamixel_sdk
        sdk = dynamixel_sdk.PacketHandler(2.0)
        p = list(b'\xff\xff\xfd\x00\x01\x09\x00\x03\x74\x00\xff\xff\xfd\x00\x00\x00')
        p = sdk.addStuffing(p)
        crc = sdk.updateCRC(0, p, len(p) - 2)
        p[-2:] = [crc & 255, crc >> 8]
        self.assertEqual(bytes(p), packet('dxl2', 1, 3, b'\x74\x00\xff\xff\xfd\x00'))


if __name__ == '__main__':
    unittest.main()
