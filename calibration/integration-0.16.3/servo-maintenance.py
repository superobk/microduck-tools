#!/usr/bin/env python3
"""One-shot Linux maintenance report. Never stops services or writes servo registers.

The operator must first stop robotd separately. This CLI is never exposed through HTTP.
Only PING and READ instructions may reach the SDK transport.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import termios
import time

sys.path.insert(0,str(Path(__file__).resolve().parent/'web'))
from servo_health import IDS, REGISTER_SIZES, atomic_json, decode_registers, utc


def service_inactive():
    result=subprocess.run(['systemctl','show','robotd.service','-p','ActiveState','--value'],
                          capture_output=True,text=True,timeout=2,check=True)
    if result.stdout.strip() not in ('inactive','failed'):
        raise RuntimeError('robotd is active/starting/stopping or state is unknown; refusing maintenance access')


def other_users(port, own_fd=None):
    """Fail closed if another process's descriptors cannot be inspected (run as root)."""
    target=Path(port).stat();users=[]
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():continue
        try:
            for entry in (process/'fd').iterdir():
                if int(process.name)==os.getpid() and entry.name==str(own_fd):continue
                try:st=entry.stat()
                except FileNotFoundError:continue
                if st.st_rdev==target.st_rdev and st.st_mode==target.st_mode:users.append(process.name)
        except (FileNotFoundError,ProcessLookupError):continue
    if users:raise RuntimeError('serial port is in use; refusing concurrent access')


def no_robotd_process():
    # Also catch manually launched robotd, independently of systemd's MainPID.
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():continue
        try:
            command=(process/'comm').read_text().strip()
            if command=='robotd':raise RuntimeError('robotd process exists; refusing maintenance access')
        except (FileNotFoundError,ProcessLookupError):continue


@contextmanager
def exclusive_port(port):
    if sys.platform!='linux' or os.geteuid()!=0:raise RuntimeError('maintenance collector requires Linux root for ownership checks')
    service_inactive();no_robotd_process();other_users(port)
    fd=os.open(port,os.O_RDWR|os.O_NOCTTY|os.O_NONBLOCK)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        fcntl.ioctl(fd,termios.TIOCEXCL)
        service_inactive();no_robotd_process();other_users(port,fd)
        yield fd
    finally:
        # Closing the last descriptor removes TIOCEXCL; no daemon/service changes here.
        os.close(fd)


class OwnedPort:
    """Small SDK PortHandler interface over the descriptor we already own exclusively."""
    def __init__(self,fd,baud,guard):
        self.fd=fd;self.guard=guard;self.is_using=False;self.is_open=True;self.baud=baud;self.deadline=0.
        speed=getattr(termios,f'B{baud}',None)
        if speed is None:raise ValueError('baud not supported by this host termios')
        attrs=termios.tcgetattr(fd);attrs[:4]=[0,0,termios.CLOCAL|termios.CREAD|termios.CS8,0]
        attrs[4]=attrs[5]=speed;attrs[6][termios.VMIN]=0;attrs[6][termios.VTIME]=0
        termios.tcsetattr(fd,termios.TCSANOW,attrs);termios.tcflush(fd,termios.TCIFLUSH)

    def clearPort(self):termios.tcflush(self.fd,termios.TCIFLUSH)
    def writePort(self,packet):
        if len(packet)<8 or packet[7] not in (0x01,0x02):raise RuntimeError('only PING/READ packets permitted')
        self.guard()
        return os.write(self.fd,bytes(packet))
    def readPort(self,length):
        try:return os.read(self.fd,length)
        except BlockingIOError:return b''
    def setPacketTimeout(self,length):self.deadline=time.monotonic()+.034+length*10/self.baud
    def setPacketTimeoutMillis(self,ms):self.deadline=time.monotonic()+ms/1000
    def isPacketTimeout(self):return time.monotonic()>self.deadline
    def closePort(self):self.is_open=False  # exclusive_port owns and closes the fd.


class ReadOnlySDK:
    def __init__(self,fd,baud,sdk,guard=lambda:None):
        self.packet=sdk.PacketHandler(2.0)
        # Guard the actual TX packet, including accidental future calls to SDK write APIs.
        original=self.packet.txPacket
        def allowed(port,packet):
            if len(packet)<8 or packet[7] not in (0x01,0x02):
                raise RuntimeError('only DYNAMIXEL PING/READ instructions are permitted')
            return original(port,packet)
        self.packet.txPacket=allowed
        self.port=OwnedPort(fd,baud,guard)

    def read(self,ident,address,size):
        method={1:self.packet.read1ByteTxRx,2:self.packet.read2ByteTxRx,4:self.packet.read4ByteTxRx}[size]
        value,result,error=method(self.port,ident,address)
        if result!=0:raise RuntimeError(self.packet.getTxRxResult(result))
        # Alert bit is evidence, not an instruction-processing failure; still read register 70.
        if error&0x7f:raise RuntimeError(self.packet.getRxPacketError(error))
        return value

    def close(self):self.port.closePort()


def collect(reader,robot_label,guard=lambda:None):
    report=dict(schema='microduck-servo-maintenance/v1',robot_label=robot_label,collected_at=utc(),
                identity='operator_label_not_verified',read_only=True,servos=[])
    for ident in IDS:
        guard();raw={};errors=[]
        # Read model first: unsupported devices get only identity reads, no XL330 assumptions.
        for address,size in REGISTER_SIZES.items():
            if address not in (0,6) and raw.get('0') not in (1190,1200):continue
            guard()
            try:raw[str(address)]=reader.read(ident,address,size)
            except (OSError,RuntimeError) as e:errors.append(f'{address}: {e}'[:500])
        report['servos'].append(dict(id=ident,sampled_at=utc(),raw=raw,read_errors=errors,decoded=decode_registers(raw)))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',required=True);parser.add_argument('--baud',type=int,default=1_000_000)
    parser.add_argument('--robot-label',required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--confirm-maintenance',action='store_true')
    args=parser.parse_args()
    if not args.confirm_maintenance:parser.error('requires --confirm-maintenance; support the robot and stop robotd manually first')
    if not 1<=len(args.robot_label)<=120 or not 9600<=args.baud<=4_000_000:parser.error('invalid label/baud')
    if args.output.exists():parser.error('output already exists; choose a new report filename')
    with exclusive_port(args.port) as fd:
        import dynamixel_sdk
        guard=lambda:(service_inactive(),no_robotd_process())
        reader=ReadOnlySDK(fd,args.baud,dynamixel_sdk,guard)
        try:
            report=collect(reader,args.robot_label,guard)
            atomic_json(args.output,report)
        finally:reader.close()
    print(json.dumps(dict(output=str(args.output),read_only=True,servos=len(report['servos']))))


if __name__=='__main__':main()
