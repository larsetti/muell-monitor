#!/usr/bin/env python3
import re
import sqlite3, json
from datetime import datetime
from collections import Counter
from pathlib import Path

import sperrliste
import sri

DB_PATH = Path(__file__).parent / "ordnungsamt.db"
TEMPLATE = Path(__file__).parent / "template.html"
OUT_PATH = Path(__file__).parent / "index.html"

# Wartungs-Lock (Rev H-01): Die Live-Seite wird NUR veröffentlicht, wenn die
# Freigabe-Datei LIVE_FREIGEGEBEN im Repo-Root existiert. Fehlt sie (frischer
# Klon, frischer Pi, Normalzustand nach Wiederanlauf), bleibt die Wartungsseite
# bestehen und der Live-Render wird übersprungen. So kann ein automatischer
# Wiederanlauf die Wartungsseite niemals versehentlich vom Netz nehmen.
# Die Pipeline legt diese Datei NIEMALS selbst an — nur eine bewusste manuelle
# Aktion schaltet auf Live.
#   Freigeben (Live): touch Technik/LIVE_FREIGEGEBEN   (bzw. Datei anlegen)
#   Sperren (Wartung): rm Technik/LIVE_FREIGEGEBEN     (Datei löschen)
GO_LIVE_MARKER = Path(__file__).parent / "LIVE_FREIGEGEBEN"
MAINTENANCE_PATH = Path(__file__).parent / "maintenance.html"

# A-11 / Sec M-02: Fallback-Wartungsseite als Konstante im Code.
# Fehlt maintenance.html (versehentlich gelöscht, nicht ausgecheckt, Tippfehler
# beim Umbenennen), darf keine alte Live-index.html mit Ortsdaten stehenbleiben.
# Diese Seite wird dann an ihrer Stelle geschrieben. Sie ist bewusst ohne
# externe Ressourcen (keine Schriften, keine Skripte, keine Bilder), damit sie
# auch dann trägt, wenn das Verzeichnis unvollständig ist.
FALLBACK_WARTUNG_HTML = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Müll-Monitor · Wartung</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; font-family: system-ui, -apple-system, sans-serif;
  background: #1a202c; color: #cbd5e0; display: flex; align-items: center;
  justify-content: center; }
.wrap { text-align: center; padding: 40px 24px; max-width: 560px; }
h1 { font-size: 22px; font-weight: 600; color: #fff; margin-bottom: 16px; }
p { font-size: 15px; line-height: 1.7; margin-bottom: 18px; }
.footer { margin-top: 40px; padding-top: 20px;
  border-top: 1px solid rgba(255,255,255,.1); font-size: 12px; color: #718096; }
.footer a { color: #a0aec0; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Die öffentliche Ansicht ist derzeit nicht verfügbar.</h1>
  <p>Die Plattform befindet sich in einer Übergangs- und Entwicklungsphase.
     Die interaktive Karte ist in dieser Zeit nicht öffentlich erreichbar.</p>
  <div class="footer">
    Kontakt: <a href="mailto:info@muell-monitor.de">info@muell-monitor.de</a><br>
    © 2026 Lars Wittkopf · muell-monitor.de
  </div>
</div>
</body>
</html>
"""

GEO_RADIUS = 0.0015

# k-Anonymitäts-Schwelle: Hotspots mit weniger als k Meldungen werden nicht
# ins öffentliche Frontend übernommen (DSGVO Erwägungsgrund 26).
# Gespiegelt aus tracker.py — muss mit tracker.K_ANONYMITY_THRESHOLD übereinstimmen.
K_ANONYMITY_THRESHOLD = 3
WEEKDAYS_SHORT = ['Mo','Di','Mi','Do','Fr','Sa','So']
MONAT_NAMEN = ['Januar','Februar','März','April','Mai','Juni','Juli','August','September','Oktober','November','Dezember']

def raster_zentrum(lat, lon):
    """A-1 (DSFA 28.07.2026): Koordinate auf das Zentrum der ~150-m-Rasterzelle.

    In der Datenbank steht als lat_center/lon_center der Mittelwert der
    Einzelmeldungen. Der ist gebäudescharf und damit genauer, als es der Zweck
    hergibt: veröffentlicht werden soll ein Straßenabschnitt, nicht ein Haus
    (Risiko R-2). Erforderlich ist nur die Auflösung des Rasters — genau die
    liefert diese Funktion. Fachlicher Verlust entsteht keiner, weil die
    Zuordnung zur Zelle unverändert bleibt.

    Fünf Nachkommastellen, identisch zu tracker.cluster_id.
    """
    return (round(round(lat / GEO_RADIUS) * GEO_RADIUS, 5),
            round(round(lon / GEO_RADIUS) * GEO_RADIUS, 5))

def parse_datum(s):
    if not s: return None
    try: return datetime.fromisoformat(s[:10])
    except: pass
    try: return datetime.strptime(s[:10], '%d.%m.%Y')
    except: return None

def get_saison(month):
    if month in (3,4,5): return 'frühling'
    if month in (6,7,8): return 'sommer'
    if month in (9,10,11): return 'herbst'
    return 'winter'

KATEGORIE_GRUPPEN = {
    'bauschutt':     {'keywords':['bauschutt','schutt','baumaterial'],'label':'🏗 Bauschutt','color':'#8B4513','hinweis':'Typisch für Gewerbetreibende oder Bauherren — Anzeige empfehlenswert'},
    'gartenabfall':  {'keywords':['gartenabfall','grünschnitt','garten','grün'],'label':'🌿 Gartenabfall','color':'#2d7d2d','hinweis':'Hinweis auf Kleingärten oder Privatgärten in der Nähe'},
    'schrottfahrzeug':{'keywords':['schrottfahrzeug','schrottauto','kfz','fahrzeug'],'label':'🚗 Schrott-KFZ','color':'#555555','hinweis':'Häufig organisierte Ablagerung — Kennzeichen-Kontrolle empfohlen'},
    'sperrmüll':     {'keywords':['sperrmüll','sperr','sofa','matratze','kühlschrank'],'label':'🛋 Sperrmüll','color':'#8a5c00','hinweis':'Oft Privatpersonen, die Sperrmülltermin umgehen'},
    'elektroschrott':{'keywords':['elektroschrott','elektro','e-schrott'],'label':'⚡ Elektroschrott','color':'#0066aa','hinweis':'Entsorgungspflichtige Geräte — Rückgabepflicht besteht'},
    'illegal':       {'keywords':['illegal','ablagerung','wild','schwarze säcke'],'label':'🚮 Illegale Ablagerung','color':'#cc0000','hinweis':'Allgemeine illegale Entsorgung'},
}

def kategorisiere(text):
    t = (text or '').lower()
    for key, grp in KATEGORIE_GRUPPEN.items():
        if any(kw in t for kw in grp['keywords']): return key
    return None

def load_data():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    muell = conn.execute("SELECT datum,lat,lon,kategorie,betreff FROM meldungen WHERE is_muell=1 AND datum IS NOT NULL AND lat IS NOT NULL").fetchall()
    cluster_m = {}
    for row in muell:
        cid = f"{round(row['lat']/GEO_RADIUS)*GEO_RADIUS:.5f}_{round(row['lon']/GEO_RADIUS)*GEO_RADIUS:.5f}"
        if cid not in cluster_m: cluster_m[cid]=[]
        cluster_m[cid].append({'datum':row['datum'],'kategorie':row['kategorie']or'','betreff':row['betreff']or''})

    # C-03: k-Anonymitäts-Filter — Singleton-Hotspots werden nicht ins Frontend
    # übernommen (DSGVO Erwägungsgrund 26: Re-Identifikation über Bezirk+PLZ+Straße
    # ist bei einer einzigen Meldung trivial möglich).
    hotspots = [dict(r) for r in conn.execute(
        "SELECT * FROM hotspots WHERE meldungen_count >= ? ORDER BY score DESC",
        (K_ANONYMITY_THRESHOLD,)
    ).fetchall()]

    # A-7: Widerspruch nach Art. 21 — gesperrte Zellen fallen aus der
    # Veröffentlichung. tracker.py legt sie schon gar nicht mehr an; hier steht
    # die zweite Sperre, damit eine Sperrung auch dann wirkt, wenn seit ihrer
    # Eintragung kein tracker-Lauf stattgefunden hat.
    gesperrt = sperrliste.laden(conn)
    hotspots = [h for h in hotspots if h['cluster_id'] not in gesperrt]

    # H-02: Hausnummer-Strip als Render-Sicherheitsnetz — entfernt Hausnummer-
    # Suffixe aus dem strasse-Feld, bevor Daten ins Frontend gehen.
    # Fängt Fälle auf, in denen tracker.py versehentlich eine Hausnummer
    # in die DB geschrieben hat (z.B. aus dem adresse-Fallback älterer Läufe).
    # Fallback bei fehlendem plz oder bezirk: pseudonymisierter Platzhalter.
    _hn_re = re.compile(r'\s+\d+[a-zA-Z]?(\s*[-/]\s*\d+[a-zA-Z]?)?\s*$')
    for h in hotspots:
        if h.get('strasse'):
            h['strasse'] = _hn_re.sub('', h['strasse']).strip()
        if not h.get('plz') or not h.get('bezirk'):
            h['strasse'] = 'Adresse unvollständig'
        # A-1: Rundung auf das Rasterzentrum. Muss VOR allem stehen, was
        # lat_center/lon_center weiterreicht — insbesondere vor dem Aufbau der
        # Prognose-Einträge weiter unten, die diese Felder kopieren.
        if h.get('lat_center') is not None and h.get('lon_center') is not None:
            h['lat_center'], h['lon_center'] = raster_zentrum(h['lat_center'], h['lon_center'])

    for h in hotspots:
        cid = h['cluster_id']
        meldungen = cluster_m.get(cid,[])
        total = len(meldungen) or 1
        grp_list = [g for g in (kategorisiere(m['kategorie']+' '+m['betreff']) for m in meldungen) if g]
        grp_count = Counter(grp_list)
        h['kategorie_mix'] = [{'key':k,'label':KATEGORIE_GRUPPEN[k]['label'],'color':KATEGORIE_GRUPPEN[k]['color'],'hinweis':KATEGORIE_GRUPPEN[k]['hinweis'],'count':c,'pct':round(c/total*100)} for k,c in grp_count.most_common()]
        h['top_kategorie'] = grp_count.most_common(1)[0][0] if grp_count else None
        h['top_kategorie_pct'] = round(grp_count.most_common(1)[0][1]/total*100) if grp_count else 0
        weekdays = []
        for m in meldungen:
            try: weekdays.append(datetime.fromisoformat(m['datum'][:10]).weekday())
            except: pass
        h['weekday_dist']={d:0 for d in WEEKDAYS_SHORT}; h['pattern']='normal'; h['pattern_label']=''; h['auffaelligkeiten']=[]
        if weekdays:
            cnt=Counter(weekdays)
            for i,d in enumerate(WEEKDAYS_SHORT): h['weekday_dist'][d]=cnt.get(i,0)
            tw=len(weekdays); mon_r=cnt.get(0,0)/tw; wknd_r=(cnt.get(5,0)+cnt.get(6,0))/tw
            if mon_r>=0.35 and tw>=2:
                h['pattern']='montag'; h['pattern_label']=f"{int(mon_r*100)}% Montags"
                h['auffaelligkeiten'].append(f"Häufung am Montag ({int(mon_r*100)}%) — Wochenend-Ablagerungen")
            elif wknd_r>=0.35 and tw>=2:
                h['pattern']='wochenende'; h['pattern_label']=f"{int(wknd_r*100)}% Wochenende"
                h['auffaelligkeiten'].append(f"Häufung am Wochenende ({int(wknd_r*100)}%)")
        if h['top_kategorie'] and h['top_kategorie_pct']>=50:
            gi=KATEGORIE_GRUPPEN[h['top_kategorie']]
            h['auffaelligkeiten'].append(f"{gi['label']}: {h['top_kategorie_pct']}% — {gi['hinweis']}")
        if h['recurrence_count']>=3:
            h['auffaelligkeiten'].append(f"Chronischer Ablagerungsort: {h['recurrence_count']}× Wiederkehr")
        if h['pattern']=='montag' and h['top_kategorie']=='gartenabfall':
            h['auffaelligkeiten'].append("🔍 Montags + Gartenabfall → Kleingarten sehr wahrscheinlich")
        elif h['pattern']=='montag' and h['top_kategorie']=='bauschutt':
            h['auffaelligkeiten'].append("🔍 Montags + Bauschutt → Gewerbe nutzt Wochenende zur Entsorgung")
        elif h['top_kategorie']=='schrottfahrzeug' and h['recurrence_count']>=2:
            h['auffaelligkeiten'].append("🔍 Wiederkehrende KFZ-Ablagerung → Kennzeichen-Kontrolle empfohlen")

        # ── Saisonale Analyse ──────────────────────────────────────────
        seasons = []
        month_list = []
        monthend_count = 0
        for m in meldungen:
            try:
                d = datetime.fromisoformat(m['datum'][:10])
                month_list.append(d.month)
                if d.day >= 25: monthend_count += 1
                if d.month in [3,4,5]: seasons.append('frühling')
                elif d.month in [6,7,8]: seasons.append('sommer')
                elif d.month in [9,10,11]: seasons.append('herbst')
                else: seasons.append('winter')
            except: pass

        if seasons:
            season_cnt = Counter(seasons)
            top_season, top_season_n = season_cnt.most_common(1)[0]
            season_ratio = top_season_n / len(seasons)
            SEASON_LABELS = {
                'frühling': ('🌸 Frühlings-Häufung', 'Frühjahrsputz-Effekt — Gartenabfall und Sperrmüll häufen sich März–Mai'),
                'sommer':   ('☀️ Sommer-Häufung',    'Sommerzeit — häufig Gartenabfall, Grillmüll, Umzugssperrmüll'),
                'herbst':   ('🍂 Herbst-Häufung',     'Herbst — Grünschnitt und Gartenabfall nach der Gartensaison'),
                'winter':   ('❄️ Winter-Häufung',     'Wintermonate — oft Sperrmüll und Elektroschrott nach Weihnachten'),
            }
            if season_ratio >= 0.6 and len(seasons) >= 3:
                lbl, hint = SEASON_LABELS[top_season]
                h['auffaelligkeiten'].append(f"{lbl} ({int(season_ratio*100)}%) — {hint}")
            # Kombination Saison + Kategorie
            if top_season in ['frühling','herbst'] and h['top_kategorie'] == 'gartenabfall' and season_ratio >= 0.5:
                h['auffaelligkeiten'].append("🔍 Saison + Gartenabfall → saisonaler Ablagerungspunkt, Kontrolle im Frühjahr/Herbst erhöhen")
            if top_season == 'winter' and h['top_kategorie'] == 'elektroschrott':
                h['auffaelligkeiten'].append("🔍 Winter + Elektroschrott → nach Weihnachten typisch, Aufklärungskampagne sinnvoll")

        # ── Monatsende-Analyse ─────────────────────────────────────────
        if month_list and len(meldungen) >= 3:
            monthend_ratio = monthend_count / len(meldungen)
            if monthend_ratio >= 0.5:
                h['auffaelligkeiten'].append(f"📅 {int(monthend_ratio*100)}% der Meldungen zum Monatsende (ab dem 25.) — Hinweis auf Wohnungswechsel/Umzüge")
            # Monats-Häufung: immer derselbe Monat?
            month_cnt = Counter(month_list)
            top_month, top_month_n = month_cnt.most_common(1)[0]
            if top_month_n / len(month_list) >= 0.5 and len(month_list) >= 3:
                MONTH_NAMES = {1:'Januar',2:'Februar',3:'März',4:'April',5:'Mai',6:'Juni',
                               7:'Juli',8:'August',9:'September',10:'Oktober',11:'November',12:'Dezember'}
                h['auffaelligkeiten'].append(f"📅 Häufung im {MONTH_NAMES[top_month]} — möglicher periodischer Ablagerungsrhythmus")

        # ── Gemischte Kategorien (Schmuggelpunkt) ─────────────────────
        if total >= 4:
            unique_kats = len(set(m['kategorie'] for m in meldungen if m['kategorie']))
            mix_ratio = unique_kats / total
            if mix_ratio >= 0.7 and unique_kats >= 3:
                h['auffaelligkeiten'].append(f"🚨 Hohe Kategorienvielfalt ({unique_kats} verschiedene Müllarten) — bekannter öffentlicher Ablagerungspunkt, viele Verursacher")
            elif unique_kats >= 4:
                h['auffaelligkeiten'].append(f"⚠️ Gemischte Ablagerungen ({unique_kats} Müllarten) — Standort wird von mehreren Personengruppen genutzt")

    bezirk_stats = [dict(r) for r in conn.execute("""
        SELECT bezirk, COUNT(*) as total_hotspots, SUM(meldungen_count) as total_meldungen,
               SUM(recurrence_count) as total_recurrence, ROUND(MAX(score),1) as max_score,
               SUM(CASE WHEN score_label='kritisch' THEN 1 ELSE 0 END) as krit,
               SUM(CASE WHEN score_label='hoch' THEN 1 ELSE 0 END) as hoch
        FROM hotspots
        WHERE cluster_id NOT IN (SELECT cluster_id FROM sperrliste)
        GROUP BY bezirk ORDER BY max_score DESC
    """).fetchall()]

    # H-02b: Aktualitäts-Stand = letzter ERFOLGREICHER Fetch, nicht das Render-Datum.
    # Ein Fehllauf (API-Ausfall) schreibt count_total=-1; nur Läufe mit echten
    # Daten (count_total > 0) zählen als erfolgreich. Fehlt jeder Erfolgs-Eintrag
    # (frische DB), bleibt last_update None und das Frontend zeigt keinen Stand.
    letzter_erfolg = conn.execute(
        "SELECT fetched_at FROM fetch_log WHERE count_total > 0 ORDER BY fetched_at DESC LIMIT 1"
    ).fetchone()
    last_update = letzter_erfolg["fetched_at"][:10] if letzter_erfolg else None

    conn.close()

    # ── Prognose-Berechnung ──────────────────────────────────────────────
    today = datetime.now()
    today_wd, today_month = today.weekday(), today.month
    today_kw = today.isocalendar()[1]
    today_saison = get_saison(today_month)
    prognose_heute, prognose_woche, prognose_monat = [], [], []

    for h in hotspots:
        meldungen = cluster_m.get(h['cluster_id'], [])
        if len(meldungen) < 5: continue
        wd_counts, month_counts, kw_counts, parsed = [0]*7, [0]*13, {}, []
        for m in meldungen:
            d = parse_datum(m['datum'])
            if not d: continue
            wd_counts[d.weekday()] += 1
            month_counts[d.month] += 1
            kw = d.isocalendar()[1]
            kw_counts[kw] = kw_counts.get(kw, 0) + 1
            parsed.append(d)
        total = len(parsed)
        if total < 5: continue

        wd_prob = round(wd_counts[today_wd] / total * 100)
        month_prob = round(month_counts[today_month] / total * 100)
        kw_prob = min(round(kw_counts.get(today_kw, 0) / total * 100 * 3), 100)
        saison_ratio = sum(1 for d in parsed if get_saison(d.month) == today_saison) / total
        combined_prob = round(wd_prob*0.5 + month_prob*0.3 + saison_ratio*100*0.2)

        base = {k: h.get(k) for k in ('cluster_id','bezirk','strasse','plz','score_label',
                'meldungen_count','recurrence_count','lat_center','lon_center','top_kategorie')}

        if combined_prob >= 15:
            parts = []
            if wd_prob >= 15: parts.append(f"{wd_prob}% an {WEEKDAYS_SHORT[today_wd]}")
            if month_prob >= 15: parts.append(f"{month_prob}% im {MONAT_NAMEN[today_month-1]}")
            if saison_ratio >= 0.4: parts.append(f"{int(saison_ratio*100)}% im {today_saison.capitalize()}")
            prognose_heute.append({**base, 'prob': combined_prob,
                'grund': " · ".join(parts) if parts else f"{combined_prob}% Wahrscheinlichkeit"})
        if kw_prob >= 15:
            prognose_woche.append({**base, 'prob': kw_prob,
                'grund': f"KW {today_kw}: {kw_prob}% basierend auf Vorjahresdaten"})
        if month_prob >= 15:
            prognose_monat.append({**base, 'prob': month_prob,
                'grund': f"{month_prob}% aller Meldungen im {MONAT_NAMEN[today_month-1]}"})

    sort_key = lambda x: (-x['prob'], -x['meldungen_count'])
    prognose_heute.sort(key=sort_key)
    prognose_woche.sort(key=sort_key)
    prognose_monat.sort(key=sort_key)

    prognose = {
        'heute': prognose_heute[:50], 'woche': prognose_woche[:50], 'monat': prognose_monat[:50],
        'datum': today.strftime('%d.%m.%Y'), 'wochentag': WEEKDAYS_SHORT[today_wd],
        'kw': today_kw, 'monat_name': MONAT_NAMEN[today_month-1],
    }

    return {
        "hotspots": hotspots, "bezirk_stats": bezirk_stats,
        "bezirke": sorted(set(h['bezirk'] for h in hotspots if h['bezirk'])),
        "kategorie_gruppen": {k:{'label':v['label'],'color':v['color']} for k,v in KATEGORIE_GRUPPEN.items()},
        "prognose": prognose,
        "last_update": last_update,
    }

def pruefe_sri():
    """A-12: Prüfsummen der eingebundenen Dateien gegen die Dateien selbst.

    Läuft vor jedem Live-Render. Passt eine Prüfsumme nicht mehr (etwa weil
    eine Bibliothek unter assets/vendor/ ausgetauscht, die Prüfsumme aber
    nicht nachgezogen wurde), bricht der Render ab. Sonst ginge eine Seite
    online, deren Karte im Browser stumm nicht lädt.
    """
    tmpl = TEMPLATE.read_text(encoding='utf-8')
    fehler = sri.pruefe_html(tmpl, TEMPLATE.parent)
    if fehler:
        raise RuntimeError(
            "Pruefsumme passt nicht zur ausgelieferten Datei:\n  "
            + "\n  ".join(fehler)
            + "\n  Korrektur: python sri.py --schreiben"
        )
    ohne = sri.skripte_ohne_integritaet(tmpl)
    if ohne:
        print(f"  Hinweis: {len(ohne)} Einbindung(en) ohne Pruefsumme: {ohne}")

def render_live():
    """Rendert die Live-Karte aus der DB nach OUT_PATH (index.html)."""
    pruefe_sri()
    print(f"Lade Daten aus {DB_PATH}...")
    data = load_data()
    print(f"  {len(data['hotspots'])} Hotspots")
    compact = json.dumps(data, ensure_ascii=False, separators=(',',':'))
    # HTML-Sonderzeichen im JSON escapen, damit </script>-Tags den Block nicht brechen (C-04)
    compact = compact.replace('</', r'<\/')
    tmpl = TEMPLATE.read_text(encoding='utf-8')
    last_update_str = data['last_update'] or 'unbekannt'
    html = tmpl.replace('__APP_DATA_PLACEHOLDER__', compact).replace('__LAST_UPDATE__', last_update_str)
    OUT_PATH.write_text(html, encoding='utf-8')
    print(f"  Gespeichert: {OUT_PATH}")

def _schreibe_wenn_abweichend(inhalt: str) -> bool:
    """Schreibt inhalt nach OUT_PATH, wenn dort etwas anderes steht. True = geschrieben."""
    if OUT_PATH.exists() and OUT_PATH.read_text(encoding='utf-8') == inhalt:
        return False
    OUT_PATH.write_text(inhalt, encoding='utf-8')
    return True

def main() -> int:
    """Rückgabewert ist der Exit-Code.

    0 = Normalfall (Wartungsseite steht oder Live-Seite gerendert)
    2 = A-11: maintenance.html fehlte, Fallback-Wartungsseite geschrieben

    Wichtig für die Launcher: bei Exit-Code 2 muss index.html trotzdem
    committet und gepusht werden. Der Fallback IST die Abhilfe — würde der
    Launcher den Push überspringen, bliebe die alte Live-Seite mit Ortsdaten
    öffentlich stehen. Exit-Code 2 heißt also "veröffentlichen und danach
    nachsehen", nicht "abbrechen".
    """
    # Wartungs-Lock (Rev H-01): ohne Freigabe-Marker bleibt die Wartungsseite
    # stehen und der Live-Render wird übersprungen. Default ist Lock AN.
    if not GO_LIVE_MARKER.exists():
        print(f"Wartungs-Lock aktiv (kein {GO_LIVE_MARKER.name}), Live-Render übersprungen.")
        if MAINTENANCE_PATH.exists():
            wartung = MAINTENANCE_PATH.read_text(encoding='utf-8')
            # Idempotent: nur schreiben, wenn index.html abweicht oder fehlt.
            if _schreibe_wenn_abweichend(wartung):
                print(f"  Wartungsseite nach {OUT_PATH.name} geschrieben.")
            else:
                print(f"  {OUT_PATH.name} ist bereits die Wartungsseite, unverändert.")
            return 0
        # A-11 / Sec M-02: harter Abbruch statt passivem Stehenlassen.
        # Ohne Wartungsquelle wird die eingebaute Fallback-Seite geschrieben,
        # damit eine eventuell vorhandene alte Live-index.html mit Cluster-IDs,
        # Straßen und Koordinaten nicht öffentlich stehenbleibt.
        if _schreibe_wenn_abweichend(FALLBACK_WARTUNG_HTML):
            print(f"  FEHLER: {MAINTENANCE_PATH.name} fehlt. "
                  f"Eingebaute Fallback-Wartungsseite nach {OUT_PATH.name} geschrieben.")
        else:
            print(f"  FEHLER: {MAINTENANCE_PATH.name} fehlt. "
                  f"{OUT_PATH.name} ist bereits die Fallback-Wartungsseite.")
        print(f"  {MAINTENANCE_PATH.name} wiederherstellen, dann erneut ausführen. Exit-Code 2.")
        return 2
    print(f"Freigabe-Marker {GO_LIVE_MARKER.name} vorhanden, rendere Live-Seite.")
    render_live()
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
