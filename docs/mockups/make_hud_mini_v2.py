from PIL import Image, ImageDraw, ImageFont, ImageFilter
import os

S = 2
F  = "C:/Windows/Fonts/msyh.ttc"
FB = "C:/Windows/Fonts/msyhbd.ttc"
FM = "C:/Windows/Fonts/consola.ttf"

# 1x fonts for measuring
m_title = ImageFont.truetype(FB, 14)
m_key   = ImageFont.truetype(FM, 11)
m_lbl   = ImageFont.truetype(F, 12)
m_pill  = ImageFont.truetype(FB, 12)

def tw(s, font):
    b = font.getbbox(s)
    return b[2]-b[0]

PAD_X, GAP, CHIP_H, CHIP_PAD, KEY_GAP = 14, 8, 26, 10, 6
items = [("F6","启动/暂停"), ("F7","停止"), ("F8","录制")]
widths = [CHIP_PAD + tw(k,m_key) + KEY_GAP + tw(v,m_lbl) + CHIP_PAD for k,v in items]
card_w = PAD_X*2 + sum(widths) + GAP*(len(items)-1)
card_h = 76

pill_txt = "运行中"
pill_w = 12 + tw(pill_txt, m_pill) + 12

print("card", card_w, card_h, "chips", widths, "pill", pill_w)

# 2x fonts
f_title = ImageFont.truetype(FB, 14*S)
f_key   = ImageFont.truetype(FM, 11*S)
f_lbl   = ImageFont.truetype(F, 12*S)
f_pill  = ImageFont.truetype(FB, 12*S)
f_cap   = ImageFont.truetype(F, 11*S)
f_capb  = ImageFont.truetype(FB, 14*S)

CW, CH = card_w*S, card_h*S
canvas_w, canvas_h = 860, 400
img = Image.new("RGBA", (canvas_w, canvas_h), (10,12,16,255))
dr = ImageDraw.Draw(img)
for y in range(canvas_h):
    t = y/canvas_h
    dr.line([(0,y),(canvas_w,y)], fill=(int(12+10*t), int(15+11*t), int(20+14*t),255))
for x in range(0, canvas_w, 40):
    dr.line([(x,0),(x,canvas_h)], fill=(255,255,255,6))
for y in range(0, canvas_h, 40):
    dr.line([(0,y),(canvas_w,y)], fill=(255,255,255,6))

ox, oy = (canvas_w-CW)//2, 176

sh = Image.new("RGBA", (canvas_w, canvas_h), (0,0,0,0))
ImageDraw.Draw(sh).rounded_rectangle([ox-8, oy+12, ox+CW+8, oy+CH+20], radius=16*S, fill=(0,0,0,150))
img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(12)))
dr = ImageDraw.Draw(img)

dr.rounded_rectangle([ox, oy, ox+CW, oy+CH], radius=12*S, fill=(20,24,31,247),
                     outline=(43,51,65,255), width=S)
dr.line([(ox+18*S, oy+S//2+1), (ox+CW-18*S, oy+S//2+1)], fill=(255,255,255,14), width=S)

# logo
lx, ly, LS = ox+14*S, oy+13*S, 17*S
dr.rounded_rectangle([lx, ly, lx+LS, ly+LS], radius=5*S, fill=(56,132,255,255))
dr.line([(lx+4*S, ly+11*S), (lx+7*S, ly+6*S), (lx+10*S, ly+9*S), (lx+13*S, ly+5*S)],
        fill=(255,255,255,255), width=max(1,int(1.6*S)), joint="curve")

dr.text((lx+LS+7*S, ly - 1*S), "WizFlow", font=f_title, fill=(232,237,246,255))

# pill right aligned inside card
ph = 21*S
px2 = ox+CW-12*S
py = oy+11*S
pw = pill_w*S
dr.rounded_rectangle([px2-pw, py, px2, py+ph], radius=ph//2, fill=(34,197,94,32),
                     outline=(34,197,94,96), width=max(1,S//2))
r = 3*S
dcx, dcy = px2-pw+10*S+r, py+ph//2
dr.ellipse([dcx-r, dcy-r, dcx+r, dcy+r], fill=(74,222,128,255))
dr.text((dcx+r+5*S, py + (ph-14*S)//2 - S), pill_txt, font=f_pill, fill=(134,239,172,255))

# chips
cx = ox+PAD_X*S
cy = oy+40*S
for (k,v), w in zip(items, widths):
    wS = w*S
    dr.rounded_rectangle([cx, cy, cx+wS, cy+CHIP_H*S], radius=7*S, fill=(31,37,48,255),
                         outline=(49,58,74,255), width=max(1,S//2))
    kw = tw(k, m_key)*S; vw = tw(v, m_lbl)*S
    total = kw + KEY_GAP*S + vw
    tx = cx + (wS-total)//2
    ty = cy + (CHIP_H*S - 15*S)//2 + 1*S
    dr.text((tx, ty), k, font=f_key, fill=(125,211,252,255))
    dr.text((tx+kw+KEY_GAP*S, ty), v, font=f_lbl, fill=(216,224,236,255))
    cx += wS + GAP*S

dr.text((ox, oy-54), "WizFlow 悬浮窗 · 迷你档", font=f_capb, fill=(228,233,241,255))
dr.text((ox, oy-28), f"实际尺寸 {card_w} × {card_h} px  ·  默认右上角  ·  图中 2 倍渲染", font=f_cap, fill=(158,170,188,255))
dr.text((ox, oy+CH+22), "已删底注  ·  三键等间距  ·  卡片右缘 = F8 右缘  ·  状态胶囊不越界", font=f_cap, fill=(146,158,176,255))

out = r"D:\Users\Documents\AI工作\WizFlow-按键精灵\WizFlow\docs\mockups\hud_mini_v2.png"
img.convert("RGB").save(out, quality=95)
print(out, os.path.getsize(out))


