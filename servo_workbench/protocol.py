"""Bounded independent codecs; no SDK example can send hidden writes."""
from .profiles import Refused


def crc16(data):
    crc=0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc=((crc<<1)^0x8005)&0xffff if crc&0x8000 else (crc<<1)&0xffff
    return crc


def stuff(data):
    return bytes(data).replace(b"\xff\xff\xfd",b"\xff\xff\xfd\xfd")


def unstuff(data):
    return bytes(data).replace(b"\xff\xff\xfd\xfd",b"\xff\xff\xfd")


def packet(protocol, ident, instruction, params=b""):
    if type(ident) is not int or not 0<=ident<254 or len(params)>240:
        raise Refused("PACKET","广播或过长数据包被拒绝")
    if protocol=="feetech":
        body=bytes([ident,len(params)+2,instruction])+bytes(params)
        return b"\xff\xff"+body+bytes([(~sum(body))&255])
    if protocol!="dxl2":
        raise Refused("PROTOCOL","未知协议")
    body=stuff(bytes([instruction])+bytes(params))
    data=b"\xff\xff\xfd\x00"+bytes([ident])+(len(body)+2).to_bytes(2,"little")+body
    return data+crc16(data).to_bytes(2,"little")


class Decoder:
    def __init__(self, protocol):
        self.protocol=protocol
        self.buffer=bytearray()
        self.bad=0

    def feed(self, data):
        self.buffer.extend(data)
        if len(self.buffer)>4096:
            self.buffer=self.buffer[-4096:]
            self.bad+=1
        out=[]
        header=b"\xff\xff" if self.protocol=="feetech" else b"\xff\xff\xfd\x00"
        while len(self.buffer)>=len(header):
            if not self.buffer.startswith(header):
                del self.buffer[0];self.bad+=1;continue
            if len(self.buffer)<(4 if self.protocol=="feetech" else 7):break
            if self.protocol=="feetech":
                ident,n=self.buffer[2],self.buffer[3];total=n+4
            else:
                ident=self.buffer[4];n=int.from_bytes(self.buffer[5:7],"little");total=n+7
            if ident>=254 or not 2<=n<=250:
                del self.buffer[0];self.bad+=1;continue
            if len(self.buffer)<total:break
            p=bytes(self.buffer[:total])
            valid=(sum(p[2:])&255)==255 if self.protocol=="feetech" else crc16(p[:-2])==int.from_bytes(p[-2:],"little")
            if not valid:
                del self.buffer[0];self.bad+=1;continue
            del self.buffer[:total]
            body=p[4:-1] if self.protocol=="feetech" else unstuff(p[7:-2])
            if self.protocol=="dxl2":
                if len(body)<2 or body[0]!=0x55:
                    self.bad+=1;continue
                body=body[1:]
            out.append(dict(id=ident,error=body[0],data=body[1:]))
        return out
