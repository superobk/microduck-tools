"""Run with optional pinned SDK installed. Captures packets in memory, no physical port."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from test_servo_health import maintenance

try:import dynamixel_sdk as sdk
except ImportError:sdk=None


@unittest.skipIf(sdk is None,'optional DYNAMIXEL SDK transport test; run in maintenance requirements venv')
class SDKTests(unittest.TestCase):
    def test_real_sdk_reads_and_blocks_all_write_reboot_packets(self):
        packet=sdk.PacketHandler(2.0)
        class Port:
            def __init__(self):self.is_using=False;self.sent=[];self.buffer=b''
            def clearPort(self):pass
            def writePort(self,data):
                self.sent.append(list(data));ident=data[4];size=data[10]+256*data[11]
                body=[255,255,253,0,ident,size+4,0,0x55,0x80]+[0x24]+[0]*(size-1)
                crc=packet.updateCRC(0,body,len(body));self.buffer=bytes(body+[crc&255,crc>>8]);return len(data)
            def readPort(self,n):data=self.buffer[:n];self.buffer=self.buffer[n:];return data
            def setPacketTimeout(self,n):pass
            def isPacketTimeout(self):return not self.buffer
            def closePort(self):pass
        port=Port()
        with patch.object(maintenance,'OwnedPort',return_value=port):reader=maintenance.ReadOnlySDK(1,1_000_000,sdk)
        self.assertEqual(reader.read(20,70,1),36)  # Alert bit does not hide the detailed fault register.
        self.assertEqual(reader.read(20,126,2),36)
        self.assertEqual(reader.read(20,132,4),36)
        for address in (64,116):
            with self.assertRaises(RuntimeError):reader.packet.write1ByteTxRx(port,20,address,1)
        with self.assertRaises(RuntimeError):reader.packet.reboot(port,20)
        self.assertEqual([p[7] for p in port.sent],[2,2,2])


if __name__=='__main__':unittest.main()
