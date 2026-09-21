#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build an ICO with PIL (Windows-proven) and forensically check its raw DIB
orientation to learn the convention Windows expects."""
import os, struct
from PIL import Image

BASE = r'D:/软件/XianRenZhangAgent'
src = Image.open(os.path.join(BASE, '__xianrenzhang_icon.png')).convert('RGBA')

out = os.path.join(BASE, '_pil_ref.ico')
img = src.copy()
img.save(out, format='ICO', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
print('PIL ICO written', os.path.getsize(out))

d = open(out, 'rb').read()
reserved, type_, n = struct.unpack('<HHH', d[:6])
print('PIL ICO header reserved', reserved, 'type', type_, 'count', n)
PNG_MAGIC = b'\x89PNG'
off = 6
for i in range(n):
    bw, bh, bc, rsv, planes, bpp, sz, o = struct.unpack('<BBBBHHII', d[off:off+16])
    w = bw or 256; h = bh or 256
    magic = d[o:o+4]
    is_png = (magic == PNG_MAGIC)
    bh2 = struct.unpack('<i', d[o+8:o+12])[0]
    print(f'  entry{i}: {w}x{h} bpp={bpp} png={is_png} sz={sz} biHeight={bh2}')
    off += 16

def xor_to_img(xor, size, flip):
    im = Image.new('RGBA', (size, size)); px = im.load()
    for row in range(size):
        src_row = (size-1-row) if flip else row
        base = src_row*size*4
        for x in range(size):
            b,g,r,a = xor[base+x*4:base+x*4+4]
            px[x,row] = (r,g,b,a)
    return im

def brown_ratio(im):
    w,h=im.size; px=im.load(); c=0; n=0
    for y in range(h//2, h):
        for x in range(0,w,4):
            r,g,b,a=px[x,y]
            if a<40: continue
            if r>80 and r>b+30 and g>b and r>=g and not(g>180 and r>180): c+=1
            n+=1
    return c/max(n,1)

off=6; tgt=None
for i in range(n):
    bw,bh,bc,rsv,planes,bpp,sz,o=struct.unpack('<BBBBHHII', d[off:off+16])
    if (bw or 256)==256: tgt=o
    off+=16
xor=d[tgt+40:tgt+40+256*256*4]
print('PIL ICO bottom-up(bottom-half brown):', round(brown_ratio(xor_to_img(xor,256,flip=True)),4))
print('PIL ICO topdown  (bottom-half brown):', round(brown_ratio(xor_to_img(xor,256,flip=False)),4))
print('SOURCE bottom-half brown = 0.5504 -> the matching convention is what Windows uses')
