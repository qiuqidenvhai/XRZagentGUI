"""生成「仙人球浏览器」图标：暗色浏览器圆环 + 圆形带棱仙人球 + 顶部小花。
设计：球体圆润，竖棱作为「接缝」线，少量刺点，顶部醒目花朵。
"""
import math
from PIL import Image, ImageDraw

S = 512
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

cx, cy = S // 2, S // 2

# ---------- 暗色浏览器圆盘 + 亮绿环 ----------
R = 238
d = ImageDraw.Draw(img)
d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=(18, 46, 38, 255))
rin = R - 26
d.ellipse([cx - rin, cy - rin, cx + rin, cy + rin], fill=(12, 33, 28, 255))
d.ellipse([cx - R, cy - R, cx + R, cy + R], outline=(63, 174, 122, 255), width=14)
d.ellipse([cx - (R - 22), cy - (R - 22), cx + (R - 22), cy + (R - 22)],
          outline=(40, 120, 86, 170), width=4)

# ---------- 圆形仙人球（圆，不是椭圆） ----------
bx, by = cx, cy + 22
b_r = 94
ball = (76, 195, 126, 255)
d.ellipse([bx - b_r, by - b_r, bx + b_r, by + b_r], fill=ball)

# 球体柔和阴影：右下深
sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
sd = ImageDraw.Draw(sh)
sd.chord([bx - b_r, by - b_r, bx + b_r, by + b_r],
         start=10, end=170, fill=(0, 0, 0, 0))
# 用渐变近似：右下加深——画一个偏置的深色椭圆
sd.ellipse([bx - b_r + 18, by - 18, bx + b_r - 6, by + b_r - 6],
           fill=(40, 150, 88, 160))
img = Image.alpha_composite(img, sh)
d = ImageDraw.Draw(img)  # 关键：复合后重新绑定 draw

# 左上高光
hi = Image.new("RGBA", (S, S), (0, 0, 0, 0))
hd = ImageDraw.Draw(hi)
hd.ellipse([bx - b_r + 18, by - b_r + 16, bx - 10, by - 30],
           fill=(170, 240, 195, 120))
img = Image.alpha_composite(img, hi)
d = ImageDraw.Draw(img)

# ---------- 竖棱（凸起的圆脊：窄长深绿椭圆，叠在球上，裁切在球内） ----------
ridge_col = (34, 138, 82, 255)      # 棱脊色：比球体略深
ridge_hi = (130, 220, 160, 140)     # 棱高光（窄亮带）
ridge_layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
rld = ImageDraw.Draw(ridge_layer)
for off in (-62, -31, 0, 31, 62):
    ox = bx + off
    # 棱主体（凸起感：稍宽，色深）
    rld.ellipse([ox - 16, by - b_r + 4, ox + 16, by + b_r - 4], fill=ridge_col)
    # 棱左侧亮线（高光）
    rld.ellipse([ox - 16 + 4, by - b_r + 6, ox + 16 - 6, by + b_r - 6],
                outline=ridge_hi, width=2)
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).ellipse([bx - b_r, by - b_r, bx + b_r, by + b_r], fill=255)
rimg = Image.new("RGBA", (S, S), (0, 0, 0, 0))
rimg.paste(ridge_layer, (0, 0), mask)
img = Image.alpha_composite(img, rimg)
d = ImageDraw.Draw(img)

# ---------- 顶部小花（在球顶正上方，最后画确保可见） ----------
fx, fy = bx, by - b_r + 6
petal_col = (236, 106, 156, 255)
center_col = (255, 216, 107, 255)
for a in range(0, 360, 72):
    rad = math.radians(a)
    px = fx + 18 * math.cos(rad)
    py = fy + 18 * math.sin(rad)
    d.ellipse([px - 11, py - 11, px + 11, py + 11], fill=petal_col)
d.ellipse([fx - 12, fy - 12, fx + 12, fy + 12], fill=center_col)
# 花心高光
d.ellipse([fx - 4, fy - 4, fx + 4, fy + 4], fill=(255, 240, 200, 255))

# ---------- 缩到 256 并保存多尺寸 ICO ----------
final = img.resize((256, 256), Image.LANCZOS)
final.save("__browser_cactus_icon.ico",
           sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                  (64, 64), (128, 128), (256, 256)])
print("WROTE __browser_cactus_icon.ico")