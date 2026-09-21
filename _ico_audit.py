"""审计 .ico 文件内部每一档位图的真实结构（是否 DIB、是否 PNG、像素是否非空）。"""
import os
import struct
import glob
import sys

APP = os.path.dirname(os.path.abspath(__file__))


def audit(path):
    data = open(path, "rb").read()
    res, typ, cnt = struct.unpack("<HHH", data[:6])
    print("=== %s  reserved=%d type=%d count=%d bytes=%d" % (
        os.path.basename(path), res, typ, cnt, len(data)))
    off = 6
    for i in range(cnt):
        (w, h, cc, rsv, planes, bpp, size, offset) = struct.unpack(
            "<BBBBHHII", data[off:off + 16])
        off += 16
        W = w or 256
        H = h or 256
        sig = data[offset:offset + 8]
        ispng = sig == b"\x89PNG\r\n\x1a\n"
        note = ""
        if ispng:
            note = "PNG"
        else:
            if size >= 40:
                hs_size, ws, hs = struct.unpack("<Iii", data[offset:offset + 12])
                (pl, bp, comp, imgsize) = struct.unpack(
                    "<HHII", data[offset + 12:offset + 24])
                hmask = (abs(hs) // 2 + 31) // 32 * 4 if abs(hs) % 2 == 0 else 0
                need = 40 + imgsize + hmask
                note = "DIB %dx%d bpp=%d imgsize=%d (need=%d, have=%d)%s" % (
                    ws, abs(hs), bp, imgsize, need, size,
                    "" if need <= size else "  <<< TRUNCATED")
                # 抽样统计 alpha 非零像素（全图抽样）
                pdata = data[offset + 40: offset + 40 + imgsize]
                nz = 0
                tot = 0
                for k in range(3, len(pdata), 4):
                    tot += 1
                    if pdata[k]:
                        nz += 1
                note += "  有效像素=%d/%d (%.0f%%)" % (
                    nz, tot, (100.0 * nz / tot) if tot else 0)
            else:
                note = "UNKNOWN size=%d" % size
        print("   [%2d] %3dx%-3d bpp=%d bytes=%-7d off=%-7d %s" % (
            i, W, H, bpp, size, offset, note))


if __name__ == "__main__":
    targets = sys.argv[1:] or sorted(
        glob.glob(os.path.join(APP, "__xianrenzhang_icon*.ico")))
    for t in targets:
        if os.path.isabs(t):
            audit(t)
        else:
            audit(os.path.join(APP, t))
        print()
