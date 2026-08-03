"""
OG-Card Generator fuer Muell-Monitor
Erzeugt og-card.png (1200x630px) mit:
- Dunklem Hintergrund (#1a1a2e)
- Links: Logo + Produktname + Claim
- Rechts: Stilisierter Berlin-Umriss + Hotspot-Punkte
- Subline mit Kennzahlen

Aufruf: python make_og_card.py
"""

from PIL import Image, ImageDraw, ImageFont
import os

ASSETS = os.path.dirname(os.path.abspath(__file__))
FONTS = "C:/Windows/Fonts/"

W, H = 1200, 630

# Farben (Muell-Monitor Branding)
BG = "#1a1a2e"
RED = "#d62828"
GOLD = "#8a6200"
GREEN_DARK = "#2d7d2d"
ORANGE = "#cc5500"
CRITICAL = "#be0000"
WHITE = "#ffffff"
LIGHT_GRAY = "#e0e0e0"
MUTED = "#8a8a9a"

def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def make_og_card():
    img = Image.new("RGB", (W, H), color=hex_to_rgb(BG))
    draw = ImageDraw.Draw(img)

    # --- Fonts ---
    try:
        font_bold_xl = ImageFont.truetype(FONTS + "segoeuib.ttf", 62)
        font_bold_lg = ImageFont.truetype(FONTS + "segoeuib.ttf", 36)
        font_bold_md = ImageFont.truetype(FONTS + "segoeuib.ttf", 24)
        font_reg_md  = ImageFont.truetype(FONTS + "segoeui.ttf",  22)
        font_reg_sm  = ImageFont.truetype(FONTS + "segoeui.ttf",  18)
        font_reg_xs  = ImageFont.truetype(FONTS + "segoeui.ttf",  15)
    except Exception:
        font_bold_xl = font_bold_lg = font_bold_md = ImageFont.load_default()
        font_reg_md = font_reg_sm = font_reg_xs = ImageFont.load_default()

    # --- Subtiler Hintergrund-Gradient (dunkelblau -> fast schwarz nach rechts) ---
    for x in range(W):
        alpha = x / W
        r = int(26 + (10 - 26) * alpha)
        g = int(26 + (10 - 26) * alpha)
        b = int(46 + (20 - 46) * alpha)
        draw.line([(x, 0), (x, H)], fill=(r, g, b))

    # --- Rote Akzentlinie oben ---
    draw.rectangle([(0, 0), (W, 6)], fill=hex_to_rgb(RED))

    # --- Logo einbetten (links oben) ---
    logo_path = os.path.join(ASSETS, "logo_color.png")
    if os.path.exists(logo_path):
        logo = Image.open(logo_path).convert("RGBA")
        logo_h = 140
        ratio = logo_h / logo.height
        logo_w = int(logo.width * ratio)
        logo = logo.resize((logo_w, logo_h), Image.LANCZOS)
        # Weissen Hintergrund-Patch fuer Logo-Lesbarkeit vermeiden - direkt compositen
        img.paste(logo, (80, 80), logo)
        text_start_y = 80 + logo_h + 20
    else:
        text_start_y = 80

    # --- Produktname ---
    draw.text((80, text_start_y), "Müll-Monitor", font=font_bold_xl, fill=hex_to_rgb(WHITE))

    # --- Claim ---
    text_start_y += 75
    claim_line1 = "Chronische Hotspots · Prognose · Muster-Erkennung"
    draw.text((80, text_start_y), claim_line1, font=font_reg_md, fill=hex_to_rgb(LIGHT_GRAY))

    text_start_y += 34
    claim_line2 = "Für Ordnungsämter und Stadtreinigung in Berlin"
    draw.text((80, text_start_y), claim_line2, font=font_reg_sm, fill=hex_to_rgb(MUTED))

    # --- Trennlinie ---
    text_start_y += 44
    draw.rectangle([(80, text_start_y), (80 + 320, text_start_y + 2)], fill=hex_to_rgb(RED))

    # --- Kennzahlen-Zeile ---
    text_start_y += 20
    stats = [
        ("8.700+", "Hotspots"),
        ("109k+", "Meldungen"),
        ("tägl.", "Aktualisierung"),
    ]
    sx = 80
    for val, lbl in stats:
        draw.text((sx, text_start_y), val, font=font_bold_md, fill=hex_to_rgb(RED))
        draw.text((sx, text_start_y + 30), lbl, font=font_reg_xs, fill=hex_to_rgb(MUTED))
        sx += 150

    # --- URL unten links ---
    draw.text((80, H - 50), "muell-monitor.de", font=font_reg_sm, fill=hex_to_rgb(MUTED))

    # --- Rechte Seite: stilisierter Berlin-Umriss ---
    # Vereinfachter polygonaler Berlin-Umriss (normalisiert auf Rechte Haelfte)
    cx, cy = 900, 315  # Mitte des rechten Bereichs
    scale = 170

    # Grober Berlin-Umriss (normalisierte Koordinaten, Quelle: vereinfachter Polygon-Pfad)
    berlin_outline = [
        (0.0, -1.0), (0.3, -0.9), (0.6, -0.8), (0.9, -0.6),
        (1.0, -0.3), (0.95, 0.1), (0.8, 0.4), (0.7, 0.7),
        (0.5, 0.9), (0.2, 1.0), (-0.1, 0.95), (-0.4, 0.85),
        (-0.7, 0.7), (-0.9, 0.4), (-1.0, 0.1), (-0.95, -0.3),
        (-0.8, -0.6), (-0.5, -0.85), (-0.2, -0.95), (0.0, -1.0),
    ]
    poly = [(int(cx + x * scale), int(cy + y * scale)) for x, y in berlin_outline]
    draw.polygon(poly, fill=(30, 35, 60), outline=(60, 65, 100))

    # --- Hotspot-Punkte auf dem Umriss ---
    hotspots = [
        # (rel_x, rel_y, level)
        (0.05, -0.2, "kritisch"),
        (-0.3, 0.1, "hoch"),
        (0.4, 0.2, "hoch"),
        (-0.1, 0.5, "mittel"),
        (0.2, -0.5, "mittel"),
        (-0.5, -0.3, "niedrig"),
        (0.55, -0.1, "kritisch"),
        (-0.2, -0.6, "hoch"),
        (0.3, 0.55, "mittel"),
        (-0.6, 0.3, "niedrig"),
        (0.1, 0.3, "kritisch"),
        (-0.35, 0.55, "mittel"),
        (0.5, 0.4, "niedrig"),
        (-0.15, -0.35, "hoch"),
        (0.25, 0.0, "mittel"),
    ]

    level_colors = {
        "kritisch": hex_to_rgb(CRITICAL),
        "hoch":     hex_to_rgb(ORANGE),
        "mittel":   hex_to_rgb(GOLD),
        "niedrig":  hex_to_rgb(GREEN_DARK),
    }
    level_radii = {
        "kritisch": 9,
        "hoch":     7,
        "mittel":   5,
        "niedrig":  4,
    }

    for rx, ry, level in hotspots:
        px = int(cx + rx * scale)
        py = int(cy + ry * scale)
        r = level_radii[level]
        col = level_colors[level]
        # Glow-Effekt (aeusserer heller Ring)
        draw.ellipse([(px-r-3, py-r-3), (px+r+3, py+r+3)],
                     fill=(col[0]//3, col[1]//3, col[2]//3))
        draw.ellipse([(px-r, py-r), (px+r, py+r)], fill=col)

    # --- Legende rechts unten ---
    lx, ly = 710, H - 100
    draw.text((lx, ly - 22), "Einstufung:", font=font_reg_xs, fill=hex_to_rgb(MUTED))
    for i, (level, col) in enumerate(level_colors.items()):
        x_off = lx + i * 110
        draw.ellipse([(x_off, ly + 2), (x_off + 10, ly + 12)], fill=col)
        draw.text((x_off + 14, ly), level.capitalize(), font=font_reg_xs, fill=hex_to_rgb(MUTED))

    # --- Rote Akzentlinie unten ---
    draw.rectangle([(0, H - 4), (W, H)], fill=hex_to_rgb(RED))

    # Speichern
    out_path = os.path.join(ASSETS, "og-card.png")
    img.save(out_path, "PNG", optimize=True)
    print(f"Gespeichert: {out_path} ({W}x{H})")
    return out_path

if __name__ == "__main__":
    make_og_card()
