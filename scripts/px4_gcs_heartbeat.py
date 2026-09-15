"""GCS heartbeat for PX4 instances 1..N.

cognitiveos-heartbeat hardcodes 127.0.0.1:18570, which only serves PX4
instance 0. Multi-vehicle runs put the vehicles on instances 1..N, whose GCS
ports are 18570+instance, so none of them receive that heartbeat. Same
MAVLink v2 HEARTBEAT bytes as the container, fanned out to every port.
"""

import socket
import sys
import time

MAV_TYPE_GCS, MAV_AUTOPILOT_INVALID, MAV_STATE_ACTIVE = 6, 8, 4
CRC_EXTRA = 50


def x25crc(data):
    crc = 0xFFFF
    for b in data:
        tmp = b ^ (crc & 0xFF)
        tmp = (tmp ^ (tmp << 4)) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return crc


def heartbeat(seq):
    payload = (0).to_bytes(4, "little") + bytes([MAV_TYPE_GCS, MAV_AUTOPILOT_INVALID, 0, MAV_STATE_ACTIVE, 3])
    header = bytes([0xFD, len(payload), 0, 0, seq & 0xFF, 255, 190, 0, 0, 0])
    crc = x25crc(header[1:] + payload + bytes([CRC_EXTRA]))
    return header + payload + crc.to_bytes(2, "little")


def main():
    ports = [int(a) for a in sys.argv[1:]] or [18571, 18572]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print("heartbeating to", ports, flush=True)
    seq = 0
    while True:
        for port in ports:
            sock.sendto(heartbeat(seq), ("127.0.0.1", port))
        seq += 1
        time.sleep(1.0)


if __name__ == "__main__":
    main()
