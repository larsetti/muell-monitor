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

# T-25 / T-40: Zweite Sperre neben dem Wartungs-Lock, für einen anderen Fall.
# Der Wartungs-Lock schützt davor, dass die Live-Seite versehentlich anläuft.
# Diese Marke schützt davor, dass sie ABSICHTLICH angeschaltet wird, bevor die
# Rechtstexte anwaltlich freigegeben sind (T-03, T-22). Sie steht als Kommentar
# im Vorlagentext und wird beim Freigeben von Hand entfernt — ein Handgriff,
# der bewusst am Text selbst stattfindet und nicht in einer Konfiguration, die
# man ohne Blick auf die Texte umlegt.
RECHTSTEXT_MARKE = "__RECHTSTEXT_UNGEPRUEFT__"

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
    # Siebte Gruppe, Lars-Entscheidung 15.08.2026 (T-49). Anlass war Köln: dort
    # sind Schrottfahrräder 413 von 2.242 müllnahen Meldungen (18 Prozent) und
    # passten in keine der sechs Gruppen — "Schrott-KFZ" meint Fahrzeuge mit
    # Kennzeichen und rät zur Kennzeichen-Kontrolle, "Sperrmüll" meint den
    # umgangenen Abholtermin.
    #
    # Sie steht bewusst AM ENDE. kategorisiere() nimmt die erste Gruppe, deren
    # Schlüsselwort im Text steht; jede frühere Stelle hätte bestehende
    # Berliner Zuordnungen verschoben (etwa "Abfall - Sperrmüll und
    # Schrottfahrrad", das heute Sperrmüll ist). So kommen ausschließlich
    # Meldungen dazu, die bisher gar keine Gruppe hatten: im Berliner Bestand
    # 1.754 Zeilen, die seit jeher unzugeordnet mitliefen.
    #
    # Der Plural trägt ein Ä und ist deshalb ein eigenes Schlüsselwort —
    # "schrottfahrrad" trifft "Schrottfahrräder" nicht.
    'schrottfahrrad':{'keywords':['schrottfahrrad','schrottfahrräder','fahrradleiche','fahrradskelett'],'label':'🚲 Schrottfahrräder','color':'#6b46c1','hinweis':'Dauerhaft abgestellte Räder — Entfernung setzt Kennzeichnung und Fristablauf voraus'},
}

def kategorisiere(text):
    t = (text or '').lower()
    for key, grp in KATEGORIE_GRUPPEN.items():
        if any(kw in t for kw in grp['keywords']): return key
    return None

def load_data(stadt=None):
    """Daten für die Karte. Mit ``stadt`` nur der Bestand DIESER Stadt.

    T-49 / Auflage A-19: die Städte werden getrennt dargestellt, mit eigener
    Karte, eigener Kennzahl und eigener Sperrliste. Ohne diesen Filter zeigte
    die Berliner Seite ab dem ersten Kölner Import auch Kölner Zellen und einen
    Datenstand aus Köln — genau der Fehler, den T-51 auf der Schreibseite und
    T-39 beim Datenstand geschlossen haben, nur eine Schicht weiter oben.

    Ohne Angabe bleibt es beim Gesamtbestand. Das ist das Verhalten von vorher
    und für eine Datenbank mit genau einer Stadt dasselbe Ergebnis.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    stadt_und = " AND stadt = ?" if stadt else ""
    stadt_wo = " WHERE stadt = ?" if stadt else ""
    p = (stadt,) if stadt else ()

    muell = conn.execute(
        "SELECT datum,lat,lon,kategorie,betreff FROM meldungen "
        "WHERE is_muell=1 AND datum IS NOT NULL AND lat IS NOT NULL" + stadt_und,
        p).fetchall()
    cluster_m = {}
    for row in muell:
        cid = f"{round(row['lat']/GEO_RADIUS)*GEO_RADIUS:.5f}_{round(row['lon']/GEO_RADIUS)*GEO_RADIUS:.5f}"
        if cid not in cluster_m: cluster_m[cid]=[]
        cluster_m[cid].append({'datum':row['datum'],'kategorie':row['kategorie']or'','betreff':row['betreff']or''})

    # C-03: k-Anonymitäts-Filter — Singleton-Hotspots werden nicht ins Frontend
    # übernommen (DSGVO Erwägungsgrund 26: Re-Identifikation über Bezirk+PLZ+Straße
    # ist bei einer einzigen Meldung trivial möglich).
    hotspots = [dict(r) for r in conn.execute(
        "SELECT * FROM hotspots WHERE meldungen_count >= ?" + stadt_und +
        " ORDER BY score DESC",
        (K_ANONYMITY_THRESHOLD,) + p
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
    """ + stadt_und + """
        GROUP BY bezirk ORDER BY max_score DESC
    """, p).fetchall()]

    # H-02b: Aktualitäts-Stand = letzter ERFOLGREICHER Fetch, nicht das Render-Datum.
    # Ein Fehllauf (API-Ausfall) schreibt count_total=-1; nur Läufe mit echten
    # Daten (count_total > 0) zählen als erfolgreich. Fehlt jeder Erfolgs-Eintrag
    # (frische DB), bleibt last_update None und das Frontend zeigt keinen Stand.
    # T-49: auch der Stand gilt je Stadt. Ohne den Filter stuende auf der
    # Berliner Seite der Zeitpunkt des letzten KOELNER Abrufs, obwohl aus
    # Berlin seit dem 22.04.2026 nichts mehr kommt (Befund 3 aus T-51, hier auf
    # der Leseseite).
    letzter_erfolg = conn.execute(
        "SELECT fetched_at FROM fetch_log WHERE count_total > 0" + stadt_und +
        " ORDER BY fetched_at DESC LIMIT 1", p
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

def stand_fuer_anzeige(iso: str | None) -> str:
    """T-39 / H-04: Das ISO-Datum aus dem fetch_log als deutsches Datum.

    Der Rückgabewert ersetzt __LAST_UPDATE__ in der Vorlage und steht damit
    im ausgelieferten HTML — auch ohne JavaScript. Das Frontend rechnet
    zusätzlich aus D.last_update das Alter aus und warnt ab sieben Tagen.
    Ohne erfolgreichen Abruf steht dort 'unbekannt' und nicht etwa das
    Render-Datum: eine Seite, die täglich neu gebaut wird, während die
    Quelle nichts liefert, darf sich nicht selbst als frisch ausweisen.
    """
    if not iso:
        return "unbekannt"
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return iso


# HTML-Kommentare, einschliesslich mehrzeiliger. In keiner der Vorlagen steht
# ein "<!--" oder "-->" innerhalb eines script- oder style-Blocks, das Muster
# kann also nichts abschneiden, was kein Kommentar ist. Wer eine Vorlage
# umbaut, prueft das mit -- der Test dazu haelt es fest.
_KOMMENTAR = re.compile(r"<!--.*?-->", re.S)

# Die Stil- und Skriptbloecke der Vorlagen sind ebenso ausfuehrlich
# kommentiert wie das HTML drumherum, und die Kennungen stehen dort genauso.
# In maintenance.html und template.html zusammen waren es 15 Stueck.
_BLOCK_MIT_CODE = re.compile(r"<(style|script)\b[^>]*>(.*?)</\1>", re.S | re.I)

# NUR Kommentare, die eine Zeile beginnen -- nach beliebig viel Einrueckung,
# aber ohne Code davor. Das ist die Bauart jedes dokumentierenden Kommentars
# in diesen Vorlagen und zugleich die einzige, die sich ohne einen echten
# Zerteiler fuer CSS und JavaScript sicher erkennen laesst: ein "/*" oder "//"
# mitten in einer Zeile kann in einer Zeichenkette stehen ("https://..." ist
# der haeufigste Fall) und darf nicht angefasst werden.
#
# Die eine Luecke, die bleibt: eine mehrzeilige Zeichenkette in
# Schraegstrich-Anfuehrung, deren Zeile mit "/*" oder "//" beginnt. In keiner
# Vorlage gibt es das, und der Test dazu haelt es fest.
_CODE_BLOCKKOMMENTAR = re.compile(r"^[ \t]*/\*.*?\*/[ \t]*\n?", re.S | re.M)
_CODE_ZEILENKOMMENTAR = re.compile(r"^[ \t]*//[^\n]*\n?", re.M)


def _code_kommentare_entfernen(treffer: re.Match) -> str:
    ganz, tag, inhalt = treffer.group(0), treffer.group(1), treffer.group(2)
    kopf = ganz[:ganz.index(inhalt)] if inhalt else ganz[:ganz.rindex(f"</{tag}")]
    sauber = _CODE_ZEILENKOMMENTAR.sub("", _CODE_BLOCKKOMMENTAR.sub("", inhalt))
    return f"{kopf}{sauber}</{tag}>"


def ohne_interne_kommentare(html: str) -> str:
    """Kommentare aus einer ausgelieferten Seite entfernen — HTML, CSS und JS.

    Befund vom 17.08.2026. Die Kommentare in den Vorlagen sind ausfuehrlich,
    und das ist gut so — sie sagen, warum eine Stelle so aussieht, wie sie
    aussieht. Ausgeliefert wurden sie bis hierher mit, samt interner
    Kennungen und samt der Beschreibung frueherer Schwaechen. Im Quelltext
    der Wartungsseite stand woertlich, welche Fassung "bis hierher die IP
    jedes Besuchers an Google weitergegeben" hat, dazu Verweise wie "A-12
    (DSFA 28.07.2026)", "Bericht S-06" und "Befund H-04". Alles davon ist
    behoben, und der Inhalt ist harmlos. Eine oeffentliche Seite muss ihre
    eigene Fehlerhistorie trotzdem nicht mitliefern: sie nennt Datumsstaende
    und interne Ordnungsnummern, aus denen sich ablesen laesst, wann was
    offen war.

    ALLE Kommentare, nicht nur die mit Kennung. Eine Liste von Mustern
    muesste jemand pflegen, und der erste Kommentar, der eine Schwaeche
    beschreibt, ohne eine Kennung zu nennen, ginge still durch. Der Besucher
    braucht keinen einzigen davon, die Vorlage behaelt jeden.

    HTML, CSS UND JAVASCRIPT, weil die Kennungen in allen dreien stehen: 14 in
    den Stil- und Skriptbloecken von template.html, eine im Stilblock von
    maintenance.html. Ein Filter nur fuer HTML-Kommentare haette den
    auffaelligsten Teil erwischt und den groesseren stehen gelassen.

    Die Anker, an denen wartungsseite_fuer die Stadt-Texte einsetzt, sind
    echte HTML-Elemente und keine Kommentare — diese Funktion fasst sie nicht
    an. Sie laeuft trotzdem erst NACH dem Einsetzen, damit die Reihenfolge
    keine Rolle spielt.

    Was sie NICHT ist: eine Verkleinerung der Seite. Dass die Wartungsseite
    dabei um rund ein Fuenftel kuerzer wird, ist eine Nebenwirkung und kein
    Zweck — es wird nichts zusammengezogen und nichts umbenannt.
    """
    return _BLOCK_MIT_CODE.sub(_code_kommentare_entfernen,
                               _KOMMENTAR.sub("", html))


def render_live(ziel: Path | None = None, praefix: str = "", stadt=None):
    """Rendert die Live-Karte aus der DB.

    Ohne Argumente unverändert wie bisher: Ausgabe nach OUT_PATH, Verweise auf
    assets/ bleiben stehen. Mit ziel und praefix rendert dieselbe Karte in ein
    Stadt-Unterverzeichnis (T-49), wo assets/ eine Ebene höher liegt.
    ``stadt`` grenzt den Bestand auf eine Stadt ein (A-19).
    """
    pruefe_sri()
    print(f"Lade Daten aus {DB_PATH}"
          f"{' (nur ' + stadt + ')' if stadt else ''}...")
    data = load_data(stadt)
    print(f"  {len(data['hotspots'])} Hotspots")
    compact = json.dumps(data, ensure_ascii=False, separators=(',',':'))
    # HTML-Sonderzeichen im JSON escapen, damit </script>-Tags den Block nicht brechen (C-04)
    compact = compact.replace('</', r'<\/')
    tmpl = TEMPLATE.read_text(encoding='utf-8')
    # T-39: Die Ersetzung lief zwischen dem Design-Umbau und dem 13.08.2026
    # ins Leere, weil der Platzhalter aus der Vorlage gefallen war. Gemerkt
    # hat das niemand, weil eine stille Nicht-Ersetzung genau aussieht wie ein
    # geglückter Lauf. Jetzt bricht der Render ab, statt eine Seite ohne
    # Datenstand zu veröffentlichen.
    if '__LAST_UPDATE__' not in tmpl:
        raise RuntimeError(
            f"{TEMPLATE.name} enthält keinen Platzhalter __LAST_UPDATE__. "
            f"Ohne ihn ginge eine Seite online, die keinen Datenstand nennt "
            f"(Befund H-04 vom 29.07.2026, T-39)."
        )
    last_update_str = stand_fuer_anzeige(data['last_update'])
    html = tmpl.replace('__APP_DATA_PLACEHOLDER__', compact).replace('__LAST_UPDATE__', last_update_str)
    if praefix:
        html = mit_asset_praefix(html, praefix)
    html = ohne_interne_kommentare(html)
    ziel = ziel or OUT_PATH
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(html, encoding='utf-8')
    print(f"  Gespeichert: {ziel} (Stand der Daten: {last_update_str})")

def _schreibe_wenn_abweichend(inhalt: str) -> bool:
    """Schreibt inhalt nach OUT_PATH, wenn dort etwas anderes steht. True = geschrieben."""
    if OUT_PATH.exists() and OUT_PATH.read_text(encoding='utf-8') == inhalt:
        return False
    OUT_PATH.write_text(inhalt, encoding='utf-8')
    return True

def _wartungsseite_schreiben() -> int:
    """Schreibt die Wartungsseite nach OUT_PATH. 0 normal, 2 mit Fallback."""
    if MAINTENANCE_PATH.exists():
        wartung = ohne_interne_kommentare(
            MAINTENANCE_PATH.read_text(encoding='utf-8'))
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


def rechtstexte_freigegeben() -> bool:
    """T-25 / T-40: Steht die Freigabe der Rechtstexte noch aus?

    Solange RECHTSTEXT_MARKE im Vorlagentext steht, gilt sie als nicht erteilt.
    """
    return RECHTSTEXT_MARKE not in TEMPLATE.read_text(encoding='utf-8')


# T-62 (15.08.2026): Der alte Einzelseiten-Weg baut nur noch diese eine Stadt.
# Er ist der Ausnahmeweg, siehe den Abschnitt zur Befehlszeile ganz unten.
LEGACY_STADT = "berlin"


def main() -> int:
    """Rückgabewert ist der Exit-Code.

    0 = Normalfall (Wartungsseite steht oder Live-Seite gerendert)
    2 = A-11: maintenance.html fehlte, Fallback-Wartungsseite geschrieben
    3 = T-25/T-40: Freigabe-Marker liegt vor, aber die Rechtstexte sind noch
        nicht anwaltlich freigegeben. Es bleibt bei der Wartungsseite.

    Wichtig für die Launcher: bei Exit-Code 2 und 3 muss index.html trotzdem
    committet und gepusht werden. Die Wartungsseite IST die Abhilfe — würde der
    Launcher den Push überspringen, bliebe die alte Live-Seite mit Ortsdaten
    öffentlich stehen. Ein Exit-Code ungleich 0 heißt also "veröffentlichen und
    danach nachsehen", nicht "abbrechen".
    """
    # Wartungs-Lock (Rev H-01): ohne Freigabe-Marker bleibt die Wartungsseite
    # stehen und der Live-Render wird übersprungen. Default ist Lock AN.
    if not GO_LIVE_MARKER.exists():
        print(f"Wartungs-Lock aktiv (kein {GO_LIVE_MARKER.name}), Live-Render übersprungen.")
        return _wartungsseite_schreiben()

    # T-25 / T-40: Der Freigabe-Marker allein genügt nicht. Ohne freigegebene
    # Rechtstexte ginge eine Seite mit Ortsbezug-Daten ohne belastbare
    # Pflichtangaben online — genau der Zustand, den T-25 als Go-Live-Blocker
    # festhält. Fail-closed: im Zweifel Wartungsseite.
    if not rechtstexte_freigegeben():
        print(f"Freigabe-Marker {GO_LIVE_MARKER.name} vorhanden, ABER die "
              f"Rechtstexte sind nicht freigegeben.")
        print(f"  {TEMPLATE.name} enthält noch die Marke {RECHTSTEXT_MARKE}.")
        print(f"  Erst nach anwaltlicher Freigabe (T-03, T-22) die Marke aus "
              f"{TEMPLATE.name} entfernen, dann erneut ausführen.")
        code = _wartungsseite_schreiben()
        print("  Es bleibt bei der Wartungsseite. Exit-Code 3.")
        return 3 if code == 0 else code

    print(f"Freigabe-Marker {GO_LIVE_MARKER.name} vorhanden, rendere Live-Seite "
          f"für {LEGACY_STADT}.")
    # T-62 (15.08.2026): Bis heute stand hier render_live() ohne Stadt, also
    # load_data(None) über den Gesamtbestand. Seit Köln im Bestand liegt, baute
    # dieser Aufruf eine Seite mit dem Titel Berlin, 4.688 statt 4.660 Zellen,
    # Ehrenfeld in der Berliner Bezirksauswahl und dem Datum des jüngsten
    # Kölner Abrufs statt des 22.04.2026. Dieser Weg ist jetzt der Ausnahmeweg
    # und rendert nur noch eine einzige, ausdrücklich benannte Stadt.
    render_live(stadt=LEGACY_STADT)
    return 0

# ─────────────────────────────────────────────────────────────────────────────
# Städte-Gerüst (T-49, vorbereitet am 14.08.2026 auf Lars-Entscheidung)
#
# Zielstruktur der ausgelieferten Seite:
#     /            Städte-Auswahl (startseite.html)
#     /berlin/     Karte Berlin, Archiv bis 22.04.2026
#     /koeln/      Karte Köln, sobald die Daten übernommen sind
#     /bonn/       später, sobald Bonn in STAEDTE steht
#
# Drei Dinge sind hier bewusst so gebaut:
#
# 1. JEDE STADT HAT IHREN EIGENEN FREIGABE-SCHALTER. Der alte Marker
#    LIVE_FREIGEGEBEN schaltet in dieser Struktur GAR NICHTS mehr frei; jede
#    Stadt braucht ihre eigene Datei (LIVE_FREIGEGEBEN_BERLIN,
#    LIVE_FREIGEGEBEN_KOELN). So kann Köln live gehen, ohne dass Berlin
#    mitgeht, und umgekehrt. Fehlt der Schalter, bleibt es bei der
#    Wartungsseite — fail-closed wie bisher.
#
# 2. DIE RECHTSTEXTE HABEN WEITERHIN GENAU EINE QUELLE. Impressum,
#    Datenschutz-Hinweis, Widerspruchsweg und der ganze Stil kommen aus
#    maintenance.html und werden beim Bauen eingesetzt. Es gibt keine zweite
#    Fassung, die auseinanderlaufen könnte. Stadt-spezifisch ist nur, was
#    stadt-spezifisch sein MUSS: Titel, Quellenangabe, Haftungssatz,
#    Datenlizenz.
#
# 3. JEDE ERSETZUNG IST FAIL-CLOSED. Findet ein Anker sich nicht mehr im
#    Quelltext, bricht der Bau ab, statt die Seite stillschweigend mit dem
#    alten Text zu schreiben. Das ist die Lehre aus T-39: eine stille
#    Nicht-Ersetzung sieht genau aus wie ein geglückter Lauf.
# ─────────────────────────────────────────────────────────────────────────────

from dataclasses import dataclass

ROOT = Path(__file__).parent
STARTSEITE_VORLAGE = ROOT / "startseite.html"

# Anker in maintenance.html. Ändert sich dort ein Satz, schlägt der Bau fehl
# und nennt den Anker — gewollt, siehe Punkt 3 oben.
ANKER_TITEL = "<title>Müll-Monitor · Wartung</title>"
# T-67 (15.08.2026): Diese beiden Anker trugen bis heute die Behauptung, die
# Erfassung laufe weiter, samt grünem Abzeichen "Datenerfassung aktiv". Sie sind
# mit der Wartungsseite zusammen berichtigt worden.
ANKER_QUELLE = ("<p>Die Berliner Quelle liefert seit dem 22.04.2026 keine neuen "
                "Meldungen. Der bis dahin erfasste Bestand bleibt erhalten, er "
                "wächst bis auf Weiteres nicht weiter. An der Anbindung weiterer "
                "Städte wird gearbeitet.</p>")
ANKER_STATUS = '<div class="status">Erfassung ruht seit dem 22.04.2026</div>'
ANKER_HAFTUNG = ("<p>Die dargestellten Daten basieren auf der OpenData-API des Berliner "
                 "Ordnungsamtes. Trotz sorgfältiger inhaltlicher Kontrolle wird keine "
                 "Gewähr für Aktualität, Richtigkeit und Vollständigkeit übernommen.</p>")
ANKER_LIZENZ = ('Daten: <a href="https://www.govdata.de/dl-de/by-2-0" '
                'target="_blank">DL-DE-BY-2.0</a>')


@dataclass(frozen=True)
class Stadt:
    """Eine Stadt der ausgelieferten Seite.

    karte_moeglich sagt, ob überhaupt Daten für eine Karte vorliegen. Solange
    das False ist, bleibt es bei der Wartungsseite, auch wenn jemand den
    Freigabe-Schalter anlegt. Eine leere Karte zu veröffentlichen wäre
    schlimmer als gar keine.

    veroeffentlicht ist eine andere Frage und liegt eine Ebene darüber (T-74,
    17.08.2026): ob die Stadt überhaupt eine öffentlich erreichbare Adresse
    bekommt. Auf False entsteht für sie keine Seite in der ausgelieferten
    Struktur und keine Kachel auf der Startseite — auch keine Wartungsseite.
    Der Eintrag in STAEDTE bleibt dabei vollständig stehen, ebenso Quelle,
    Freigabe-Schalter und Tests. Zurückholen heißt: hier True setzen und die
    Ignorier-Regel für den Ordner aus .gitignore nehmen.
    """
    slug: str
    name: str
    quelle_satz: str
    status_text: str
    status_farbe: str
    haftung_satz: str
    lizenz_url: str
    lizenz_text: str
    kachel_status: str
    kachel_marke: str
    kachel_marke_klasse: str
    karte_moeglich: bool
    veroeffentlicht: bool = True

    @property
    def marker(self) -> Path:
        return ROOT / f"LIVE_FREIGEGEBEN_{self.slug.upper()}"


STAEDTE = (
    Stadt(
        slug="berlin",
        name="Berlin",
        quelle_satz=(
            "<p>Grundlage sind die offen bereitgestellten Meldungen des Berliner "
            "Ordnungsamts. Diese Quelle liefert seit dem 22.04.2026 keine neuen "
            "Meldungen. Der erfasste Bestand bleibt erhalten, er wächst bis auf "
            "Weiteres nicht weiter.</p>"),
        status_text="Quelle seit dem 22.04.2026 ohne neue Meldungen",
        status_farbe="ruht",
        haftung_satz=(
            "<p>Die dargestellten Daten basieren auf den offen bereitgestellten "
            "Meldungen des Berliner Ordnungsamts. Trotz sorgfältiger inhaltlicher "
            "Kontrolle wird keine Gewähr für Aktualität, Richtigkeit und "
            "Vollständigkeit übernommen.</p>"),
        lizenz_url="https://www.govdata.de/dl-de/by-2-0",
        lizenz_text="DL-DE-BY-2.0",
        kachel_status="Erfasster Bestand bis zum 22.04.2026. Die Quelle liefert seit dem 22.04.2026 keine neuen Meldungen.",
        kachel_marke="Archiv",
        kachel_marke_klasse="marke-archiv",
        karte_moeglich=True,
        veroeffentlicht=True,
    ),
    Stadt(
        slug="koeln",
        name="Köln",
        quelle_satz=(
            "<p>Die Kölner Ansicht wird gerade vorbereitet. Grundlage sind die offen "
            "bereitgestellten Anliegen-Meldungen der Stadt Köln.</p>"),
        status_text="In Vorbereitung",
        status_farbe="neutral",
        haftung_satz=(
            "<p>Die dargestellten Daten basieren auf den offen bereitgestellten "
            "Anliegen-Meldungen der Stadt Köln. Trotz sorgfältiger inhaltlicher "
            "Kontrolle wird keine Gewähr für Aktualität, Richtigkeit und "
            "Vollständigkeit übernommen.</p>"),
        lizenz_url="https://www.govdata.de/dl-de/zero-2-0",
        lizenz_text="DL-DE-ZERO-2.0",
        kachel_status="Die Übernahme der Kölner Meldungen wird vorbereitet.",
        kachel_marke="In Vorbereitung",
        kachel_marke_klasse="marke-vorbereitung",
        karte_moeglich=False,
        # T-74, Lars-Entscheidung 17.08.2026: Köln bekommt vorläufig keine
        # öffentliche Adresse. Auf muell-monitor.de/koeln/ stand nur eine
        # Wartungsseite ohne jede Ortsangabe, die Sperren hatten also gehalten.
        # Sie ist trotzdem eine Vorankündigung gegenüber einer Stadt, mit der es
        # noch keinen Kontakt gibt. Berlin ist bekannt und bleibt.
        # Zurückholen (nach der anwaltlichen Freigabe, T-03): hier True setzen,
        # "koeln/" aus .gitignore nehmen, bauen, pushen. Am Rest ist nichts zu
        # tun — Quelle, Adapter, Freigabe-Schalter und Tests bleiben vollständig.
        veroeffentlicht=False,
    ),
)

# Der grüne Punkt der Vorlage pulsiert und sagt damit "läuft gerade". Für eine
# Quelle, die nichts mehr liefert, und für eine Stadt, die erst vorbereitet
# wird, ist das dieselbe Sorte Zusicherung, die T-39 und T-45 aus der Seite
# entfernt haben. Deshalb bekommt jede Statusfarbe auch den Punkt — Farbe UND
# Animation. Eine Anweisung im style-Attribut allein reicht nicht, weil der
# Punkt ein ::before-Element ist und sich so nicht erreichen lässt.
STATUS_STIL = {
    "ruht": """.status {
  background: rgba(214,40,40,.15);
  border-color: rgba(214,40,40,.35);
  color: #f8b4b4;
}
.status::before { background: #d62828; animation: none; }""",
    "neutral": """.status {
  background: rgba(255,255,255,.08);
  border-color: rgba(255,255,255,.18);
  color: #cbd5e0;
}
.status::before { background: #a0aec0; animation: none; }""",
}


def _ersetze_einmal(text: str, anker: str, ersatz: str, wofuer: str) -> str:
    """Ersetzt anker durch ersatz und bricht ab, wenn der Anker fehlt.

    Ohne diesen Abbruch entstünde eine Seite, die für die eine Stadt den Text
    der anderen trägt — und niemand würde es merken, weil das Bauen gelingt.
    """
    if anker not in text:
        raise RuntimeError(
            f"{MAINTENANCE_PATH.name}: Anker für {wofuer} nicht gefunden.\n"
            f"  Gesucht: {anker[:80]}...\n"
            f"  Wurde die Wartungsseite umformuliert? Dann den Anker in "
            f"export_html.py nachziehen, sonst bekäme eine Stadtseite den "
            f"Text einer anderen Stadt."
        )
    return text.replace(anker, ersatz, 1)


def mit_asset_praefix(html: str, praefix: str) -> str:
    """Setzt praefix vor jeden Verweis auf assets/.

    Eine Stadtseite liegt eine Ebene tiefer als assets/. Ohne diese Umschrift
    zeigen Schriften, Symbole und die Kartenbibliothek ins Leere — und zwar
    stumm: die Seite lädt, sie sieht nur falsch aus und die Karte bleibt leer.
    """
    return html.replace('="assets/', f'="{praefix}assets/')


def wartungsseite_fuer(stadt: Stadt, praefix: str = "../") -> str:
    """Baut die Wartungsseite einer Stadt aus der einen Quelle maintenance.html."""
    html = MAINTENANCE_PATH.read_text(encoding="utf-8")
    html = _ersetze_einmal(
        html, ANKER_TITEL,
        f"<title>Müll-Monitor {stadt.name} · Wartung</title>", "den Seitentitel")
    html = _ersetze_einmal(html, ANKER_QUELLE, stadt.quelle_satz, "die Quellenangabe")
    html = _ersetze_einmal(
        html, ANKER_STATUS,
        f'<div class="status">{stadt.status_text}</div>',
        "die Statuszeile")
    html = _ersetze_einmal(
        html, "</head>",
        f"<style>\n{STATUS_STIL[stadt.status_farbe]}\n</style>\n</head>",
        "das Ende des Kopfbereichs")
    html = _ersetze_einmal(html, ANKER_HAFTUNG, stadt.haftung_satz, "den Haftungssatz")
    html = _ersetze_einmal(
        html, ANKER_LIZENZ,
        f'Daten: <a href="{stadt.lizenz_url}" target="_blank">{stadt.lizenz_text}</a>',
        "die Datenlizenz")
    html = mit_asset_praefix(html, praefix) if praefix else html
    # Erst hier, nach allen Ersetzungen: die Anker sind echte HTML-Elemente,
    # aber so bleibt die Reihenfolge auch dann richtig, wenn spaeter jemand
    # einen Kommentar-Anker einfuehrt.
    return ohne_interne_kommentare(html)


def _stil_und_recht_aus_der_wartungsseite() -> tuple[str, str]:
    """Holt Stilblock und Impressum-Ebene aus maintenance.html."""
    html = MAINTENANCE_PATH.read_text(encoding="utf-8")
    for anfang, ende, wofuer in (("<style>", "</style>", "den Stilblock"),
                                 ('<div id="impOverlay"', "</body>", "die Impressum-Ebene")):
        if anfang not in html or ende not in html:
            raise RuntimeError(
                f"{MAINTENANCE_PATH.name}: {wofuer} nicht gefunden. Die Startseite "
                f"bezieht beides von dort und würde sonst ohne Pflichtangaben "
                f"gebaut.")
    stil = html[html.index("<style>"):html.index("</style>") + len("</style>")]
    recht = html[html.index('<div id="impOverlay"'):html.index("</body>")].rstrip()
    return stil, recht


def _kachel(stadt: Stadt) -> str:
    return (f'    <a class="stadt" href="{stadt.slug}/">\n'
            f'      <div class="stadt-name">{stadt.name}</div>\n'
            f'      <div class="stadt-status">{stadt.kachel_status}</div>\n'
            f'      <span class="stadt-marke {stadt.kachel_marke_klasse}">'
            f'{stadt.kachel_marke}</span>\n'
            f'    </a>')


def ausgelieferte_staedte() -> tuple[Stadt, ...]:
    """Die Städte, die eine öffentlich erreichbare Adresse bekommen (T-74).

    STAEDTE ist die vollständige Struktur und bleibt es. Diese Auswahl ist die
    Veröffentlichungsentscheidung darüber. Wer eine Stadt zurückhalten will,
    setzt ihr veroeffentlicht auf False — und muss nichts am Gerüst anfassen.
    """
    return tuple(s for s in STAEDTE if s.veroeffentlicht)


def entferne_zurueckgehaltene(ziel: Path, staedte: tuple[Stadt, ...]) -> None:
    """Räumt die Adressen zurückgehaltener Städte aus dem Zielbaum.

    Ohne diesen Schritt bliebe eine Seite, die einmal gebaut wurde, einfach
    liegen — der Bau schreibt sie nur nicht mehr neu. Bei GitHub Pages ist der
    Speicher die Seite, also wäre sie damit weiter öffentlich erreichbar, und
    der nächste "git add */index.html" eines Launchers nähme sie wieder mit.
    Deshalb wird sie hier aktiv entfernt, bei jedem Lauf.

    Scheitert das Löschen der Seite, bricht der Lauf ab und läuft in die
    Ersatzseiten-Klammer von baue_ausgeliefert. Das ist gewollt: eine Seite,
    die weg soll und nicht weggeht, darf nicht stillschweigend liegenbleiben.
    Der leere Ordner danach ist dagegen nur Ordnung und darf scheitern — unter
    OneDrive verweigert Windows das Entfernen eines Ordners regelmäßig, obwohl
    die Datei darin schon weg ist (am 17.08.2026 hier passiert, WinError 5).
    Daran soll kein Bau hängen: öffentlich erreichbar ist die Adresse ohne
    index.html ohnehin nicht, und ein leerer Ordner kommt in Git gar nicht an.
    """
    for stadt in STAEDTE:
        if stadt in staedte:
            continue
        for ordner in (ziel / stadt.slug, ziel / UMLAUT_PFADE.get(stadt.slug, stadt.slug)):
            seite = ordner / "index.html"
            if seite.exists():
                seite.unlink()
                print(f"  {stadt.name}: zurückgehalten, entfernt -> {seite}")
            if ordner.is_dir() and not any(ordner.iterdir()):
                try:
                    ordner.rmdir()
                except OSError as fehler:
                    print(f"  Hinweis: {ordner} liess sich nicht entfernen "
                          f"({fehler}). Der Ordner ist leer, die Adresse "
                          f"antwortet nicht. Kein Grund zum Abbruch.")


def baue_startseite(ziel: Path, staedte: tuple[Stadt, ...] = STAEDTE) -> None:
    """Schreibt die Städte-Auswahl nach ziel/index.html."""
    vorlage = STARTSEITE_VORLAGE.read_text(encoding="utf-8")
    stil, recht = _stil_und_recht_aus_der_wartungsseite()
    ersetzungen = (
        ("__STIL__", stil),
        ("__STAEDTE_KACHELN__", "\n".join(_kachel(s) for s in staedte)),
        ("__IMPRESSUM_BLOCK__", recht),
    )
    html = vorlage
    for platzhalter, ersatz in ersetzungen:
        # Genau einmal, nicht mindestens einmal. Am 14.08.2026 stand __STIL__
        # zusätzlich in einem CSS-Kommentar der Vorlage; die zweite Ersetzung
        # schob den kompletten Stilblock samt </style> mitten in ein
        # Stil-Element und zerlegte die Seite. Aufgefallen ist das erst im
        # Browser, weil das Bauen selbst fehlerfrei durchlief.
        anzahl = html.count(platzhalter)
        if anzahl != 1:
            raise RuntimeError(
                f"{STARTSEITE_VORLAGE.name}: Platzhalter {platzhalter} kommt "
                f"{anzahl}-mal vor, erwartet ist genau einmal. Bei 0 ginge eine "
                f"Startseite ohne Stil oder ohne Pflichtangaben online, bei "
                f"mehr als 1 wird an einer Stelle eingesetzt, die kein "
                f"Platzhalter sein sollte.")
        html = html.replace(platzhalter, ersatz, 1)
    html = ohne_interne_kommentare(html)
    ziel.mkdir(parents=True, exist_ok=True)
    (ziel / "index.html").write_text(html, encoding="utf-8")
    print(f"  Startseite geschrieben: {ziel / 'index.html'} "
          f"({len(staedte)} Städte)")


# Getippte Umlaut-Adressen, die auf einen ASCII-Pfad weiterleiten. Als Tabelle,
# weil entferne_zurueckgehaltene() dieselbe Zuordnung braucht: eine Weiterleitung
# auf eine zurückgehaltene Stadt wäre ein Verweis ins Leere.
UMLAUT_PFADE = {"koeln": "köln"}


def baue_umlaut_weiterleitung(ziel: Path,
                              staedte: tuple[Stadt, ...] = STAEDTE) -> None:
    """Legt /köln/ an, das auf /koeln/ weiterleitet.

    Die Pfade selbst bleiben ASCII (koeln, nicht köln), weil ein Umlaut im
    Pfad als %C3%B6 kodiert wird und in Mail-Programmen und Chat-Fenstern
    beim Verlinken zerbricht. Wer die Umlaut-Adresse tippt, landet trotzdem
    richtig.
    """
    for stadt, umlaut in UMLAUT_PFADE.items():
        if not any(s.slug == stadt for s in staedte):
            continue
        ordner = ziel / umlaut
        ordner.mkdir(parents=True, exist_ok=True)
        (ordner / "index.html").write_text(
            '<!DOCTYPE html>\n<html lang="de">\n<head>\n<meta charset="UTF-8">\n'
            f'<meta http-equiv="refresh" content="0; url=../{stadt}/">\n'
            f'<link rel="canonical" href="../{stadt}/">\n'
            '<meta name="robots" content="noindex">\n'
            '<title>Müll-Monitor · Weiterleitung</title>\n</head>\n<body>\n'
            f'<p>Weiter zu <a href="../{stadt}/">/{stadt}/</a>.</p>\n'
            '</body>\n</html>\n', encoding="utf-8")
        print(f"  Weiterleitung geschrieben: {ordner / 'index.html'} -> /{stadt}/")


def baue_stadt(stadt: Stadt, ziel: Path) -> int:
    """Baut eine Stadt nach ziel/<slug>/index.html.

    Rückgabewert ist der Exit-Code dieser Stadt:
      0 = Wartungsseite steht oder Live-Karte gerendert
      3 = Freigabe-Schalter liegt vor, Rechtstexte sind aber nicht freigegeben
      4 = Freigabe-Schalter liegt vor, es gibt für diese Stadt noch keine Daten
    """
    ausgabe = ziel / stadt.slug / "index.html"
    ausgabe.parent.mkdir(parents=True, exist_ok=True)

    def wartung(grund: str) -> None:
        ausgabe.write_text(wartungsseite_fuer(stadt), encoding="utf-8")
        print(f"  {stadt.name}: Wartungsseite ({grund}) -> {ausgabe}")

    if not stadt.marker.exists():
        wartung(f"kein {stadt.marker.name}")
        return 0
    if not stadt.karte_moeglich:
        wartung("Freigabe liegt vor, aber es gibt noch keine Daten")
        print(f"  {stadt.name}: {stadt.marker.name} existiert, für diese Stadt "
              f"sind aber noch keine Meldungen übernommen. Exit-Code 4.")
        return 4
    if not rechtstexte_freigegeben():
        wartung("Rechtstexte nicht freigegeben")
        print(f"  {stadt.name}: {TEMPLATE.name} enthält noch die Marke "
              f"{RECHTSTEXT_MARKE}. Exit-Code 3.")
        return 3
    print(f"  {stadt.name}: {stadt.marker.name} vorhanden, rendere Live-Karte.")
    # A-19: jede Stadtseite bekommt ausschliesslich ihren eigenen Bestand.
    render_live(ziel=ausgabe, praefix="../", stadt=stadt.slug)
    return 0


# Rangfolge der Exit-Codes, aufsteigend nach Gewicht des Grundes. Gebraucht
# wird sie in baue_staedte, das aus mehreren Stadt-Codes einen machen muss.
#
# Befund K-11 der Abnahme vom 15.08.2026: dort stand max(codes), und der
# Docstring nannte das "der schwerste". Das trifft nicht zu, weil die Zahlen
# nach Erscheinen vergeben wurden und nicht nach Gewicht. Sind beide Marker
# gesetzt und Köln hat keine Daten, liefert Berlin 3 (Rechtstexte nicht
# freigegeben) und Köln 4 (keine Daten) — zurückgegeben wurde 4, also der
# harmlosere der beiden Gründe, und der schwerere verschwand aus dem
# Protokoll.
#
# Warum 3 über 4 steht: Code 4 heißt, für eine Stadt ist noch nichts da. Code
# 3 heißt, jemand hat den Freigabe-Schalter bewusst gesetzt, und die Seite
# geht nur deshalb nicht live, weil die Rechtstexte noch nicht anwaltlich
# freigegeben sind (T-25 / T-40). Das ist der Zustand, den man im Protokoll
# sehen will. Auf die Seiten selbst wirkt sich nichts davon aus — beide
# schreiben ohnehin die Wartungsseite, und die Launcher werten den Code nicht
# aus. Es geht allein um Nachvollziehbarkeit.
EXIT_RANG = {0: 0, 4: 1, 2: 2, 3: 3}
# Ein Code, den diese Zuordnung nicht kennt, gewinnt absichtlich gegen alle
# bekannten. Wer später einen fünften Grund einführt und hier nicht nachzieht,
# bekommt ihn zu sehen statt ihn stillschweigend zu verlieren.
EXIT_RANG_UNBEKANNT = 99


def baue_staedte(ziel: Path, staedte: tuple[Stadt, ...] = STAEDTE) -> int:
    """Baut Startseite und die übergebenen Städte nach ziel.

    Der Vorgabewert ist die vollständige Struktur. So bauen der Blick von Hand
    und die Tests des Gerüsts weiter alles; der ausgelieferte Weg übergibt
    dagegen nur die veröffentlichten Städte (T-74).

    Rückgabewert ist der Code mit dem schwersten Grund, nicht der größte —
    siehe EXIT_RANG darüber.
    """
    print(f"Baue Städte-Struktur nach {ziel}")
    codes = [baue_stadt(stadt, ziel) for stadt in staedte]
    baue_umlaut_weiterleitung(ziel, staedte)
    entferne_zurueckgehaltene(ziel, staedte)
    baue_startseite(ziel, staedte)
    if (ROOT / "LIVE_FREIGEGEBEN").exists():
        print("  HINWEIS: Der alte Marker LIVE_FREIGEGEBEN liegt noch da. In "
              "dieser Struktur schaltet er nichts mehr frei — jede Stadt hat "
              "ihren eigenen Schalter.")
    return max(codes, key=lambda c: EXIT_RANG.get(c, EXIT_RANG_UNBEKANNT),
               default=0)


def baue_ausgeliefert(ziel: Path) -> int:
    """Baut die Städte-Struktur und fängt einen Abbruch mit der Ersatzseite ab.

    A-11 hing bisher am Einzelseiten-Weg: fehlt maintenance.html, schreibt
    _wartungsseite_schreiben die eingebaute Ersatzseite, damit keine alte
    Live-Seite mit Ortsdaten öffentlich stehenbleibt. Der Städte-Weg hat diese
    Vorkehrung nicht — er bricht ab, sobald ein Anker oder die Wartungsquelle
    fehlt, und das ist als Schutz vor falschem Text auch richtig. Nur würde ohne
    diese Klammer beim Wechsel des Normalwegs (T-62) genau die Zusage aus A-11
    stillschweigend wegfallen. Deshalb hier: erst der Abbruch, dann die
    Ersatzseite an jeder Adresse, die eine Karte tragen könnte.

    Dies ist zugleich der Weg, der über die Veröffentlichung entscheidet: er
    baut ausschliesslich die Städte aus ausgelieferte_staedte() (T-74). Eine
    zurückgehaltene Stadt bekommt hier keine Adresse, auch im Fehlerfall keine
    Ersatzseite — die Adresse soll ja gerade nicht antworten.
    """
    staedte = ausgelieferte_staedte()
    try:
        return baue_staedte(ziel, staedte)
    except Exception as fehler:  # noqa: BLE001 — jeder Grund führt zur Ersatzseite
        print(f"FEHLER beim Bau der Städte-Struktur: {fehler}")
        # Auch hier weg mit den zurückgehaltenen Adressen. Der eigene
        # Fehlerfang ist Absicht: diese Klammer hat eine Zusage zu halten
        # (A-11, an jeder ausgelieferten Adresse steht eine Wartungsseite).
        # Sie darf nicht daran scheitern, dass eine Datei sich nicht löschen
        # liess — dann steht der Grund im Protokoll und die Zusage trotzdem.
        try:
            entferne_zurueckgehaltene(ziel, staedte)
        except OSError as raeumfehler:
            print(f"  WARNUNG: zurückgehaltene Adressen liessen sich nicht "
                  f"räumen ({raeumfehler}). Von Hand nachsehen.")
        seiten = [ziel / "index.html"]
        seiten += [ziel / s.slug / "index.html" for s in staedte]
        for seite in seiten:
            seite.parent.mkdir(parents=True, exist_ok=True)
            if not seite.exists() or seite.read_text(encoding="utf-8") != FALLBACK_WARTUNG_HTML:
                seite.write_text(FALLBACK_WARTUNG_HTML, encoding="utf-8")
                print(f"  Ersatz-Wartungsseite geschrieben: {seite}")
            else:
                print(f"  {seite} ist bereits die Ersatz-Wartungsseite.")
        print("  Ursache beheben, dann erneut ausführen. Exit-Code 2.")
        return 2


# ─────────────────────────────────────────────────────────────────────────────
# Befehlszeile (T-62, 15.08.2026)
#
# Bis heute war es andersherum: der stadtblinde Einzelseiten-Weg war der
# Normalfall, die Städte-Struktur steckte hinter --staedte. Alle drei Launcher
# riefen deshalb "python export_html.py" auf und bauten damit eine Seite, die
# "Müll-Hotspot Monitor Berlin" heißt, aber Kölner Zellen mitzählt: 4.688 statt
# 4.660 Standorte, 75.297 statt 74.397 Meldungen, "Ehrenfeld" in der Berliner
# Bezirksauswahl und im Datenstand-Streifen der 15.08.2026 mit grünem Punkt
# statt des 22.04.2026 in Warnfarbe. Das war wörtlich Befund H-04, den T-39 am
# 13.08.2026 geschlossen hatte, nur über einen anderen Weg zurückgekommen.
#
# Warum der Normalweg wechselt und nicht bloß die Launcher den Zusatz bekommen:
# ein vergessener Zusatz baut sonst weiterhin still eine inhaltlich falsche
# Seite. Jetzt baut ein vergessener Zusatz die richtige Struktur, und wer den
# alten Weg will, muss ihn benennen. Der Fehler, der übrigbleibt, ist der
# laute.
#
#   python export_html.py                     Städte-Struktur in die Wurzel
#   python export_html.py --ziel _vorschau    dieselbe Struktur zur Ansicht
#   python export_html.py --eine-seite        alter Weg, NUR Berlin
#
# --staedte und --jetzt-umstellen werden weiter angenommen und tun nichts. Sie
# stehen in Merkzetteln, im detail von T-49 und in der Projekt-CLAUDE.md; ein
# Abbruch mit "unbekanntes Argument" wäre dort die unnötigere Überraschung.
# ─────────────────────────────────────────────────────────────────────────────

ALTLASTEN_SCHALTER = ("--staedte", "--jetzt-umstellen")


def cli(argv: list[str]) -> int:
    """Wertet die Befehlszeile aus und liefert den Exit-Code."""
    if "--eine-seite" in argv:
        print("Einzelseiten-Weg (--eine-seite): es entsteht nur "
              f"{OUT_PATH.name} für {LEGACY_STADT}, keine Städte-Struktur.")
        return main()
    ziel = ROOT
    if "--ziel" in argv:
        ziel = Path(argv[argv.index("--ziel") + 1])
    for schalter in ALTLASTEN_SCHALTER:
        if schalter in argv:
            print(f"Hinweis: {schalter} wird nicht mehr gebraucht, die "
                  f"Städte-Struktur ist seit T-62 der Normalfall.")
    return baue_ausgeliefert(ziel)


if __name__ == "__main__":
    import sys
    sys.exit(cli(sys.argv[1:]))
