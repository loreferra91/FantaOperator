from __future__ import annotations

import html
import json
import os
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from fantaoperator.analytics import possible_duplicate
from fantaoperator.assistant import respond
from fantaoperator.database import Database
from fantaoperator.engine import FORMATION_LIMITS, compare_players, optimize_bench, optimize_lineup, player_score
from fantaoperator.workspace import PLAYER_FIELDS, lineup_score, parse_roster_csv, roster_csv
from fantaoperator.sources import csv_template, safe_url
from fantaoperator.official_votes import EDITIONS
from fantaoperator.gazzetta_votes import configure_preferred_source, gazzetta_votes_url, is_gazzetta_votes_url, PROVIDER as GAZZETTA_PROVIDER, EDITION as GAZZETTA_EDITION
from fantaoperator.updater import import_votes, refresh_votes, sync_due
from fantaoperator.diretta_rosters import DIRETTA_ROSTERS_URL, PROVIDER as DIRETTA_PROVIDER, sync_diretta_rosters


st.set_page_config(
    page_title="FantaOperator",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="auto",
)


def database() -> Database:
    # Anonymous cloud visitors must never share roster, settings or chat.
    # The local launcher explicitly supplies its persistent database path.
    if "workspace_db" not in st.session_state:
        path = os.getenv("FANTAOPERATOR_DB")
        if not path:
            directory = tempfile.TemporaryDirectory(prefix="fantaoperator-")
            st.session_state["workspace_directory"] = directory
            path = str(Path(directory.name) / "workspace.db")
        st.session_state["workspace_db"] = Database(path)
    db = st.session_state["workspace_db"]
    configure_preferred_source(db)
    return db


def inject_css() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700;800&family=Inter:wght@400;500;600;700&display=swap');
        :root { --navy:#061723; --navy2:#091d2b; --surface:#0c2231; --line:#284353;
            --text:#f4f7f2; --muted:#91a4ae; --lime:#c8f20f; --cyan:#43c7dc;
            --amber:#ffab1a; --red:#ff5e57; }
        html,body,[class*="css"],[data-testid="stAppViewContainer"]{font-family:'Inter',sans-serif}
        .stApp{background:var(--navy);color:var(--text)} [data-testid="stHeader"]{background:transparent}
        [data-testid="stAppDeployButton"],[data-testid="stMainMenu"]{display:none} [data-testid="stMainBlockContainer"]{max-width:1540px;padding:4rem 2rem 4rem}
        section[data-testid="stSidebar"]{background:#04131e;border-right:1px solid var(--line);min-width:236px}
        section[data-testid="stSidebar"] [data-testid="stSidebarContent"]{padding:1.1rem .65rem}
        [data-testid="stSidebarCollapseButton"] button,[data-testid="stExpandSidebarButton"]{color:#dbe5e8!important}
        section[data-testid="stSidebar"] .stRadio label{padding:.55rem .7rem;margin:.1rem 0;border-radius:8px;color:#d8e1e5}
        section[data-testid="stSidebar"] .stRadio label p{color:#b9c7cd!important}
        section[data-testid="stSidebar"] .stRadio label:has(input:checked){background:#102635;color:var(--lime);border-left:3px solid var(--lime)}
        section[data-testid="stSidebar"] .stRadio label:has(input:checked) p{color:var(--lime)!important}
        section[data-testid="stSidebar"] .stRadio [data-testid="stMarkdownContainer"] p{font-size:.86rem}
        .brand{display:flex;align-items:center;gap:.7rem;padding:.35rem .45rem 1.2rem}.brand-mark{color:var(--navy);background:var(--lime);font-weight:900;font-size:1.2rem;width:36px;height:36px;display:grid;place-items:center;transform:skew(-8deg);border-radius:4px}.brand-copy{font:800 1.12rem/.9 'Barlow Condensed',sans-serif;letter-spacing:.04em;color:white}
        .sidebar-foot{color:var(--muted);font-size:.69rem;border-top:1px solid var(--line);padding:1rem .7rem 0;margin-top:1.5rem}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--lime);margin-right:.4rem}.dot.amber{background:var(--amber)}.dot.red{background:var(--red)}
        h1,h2,h3{font-family:'Barlow Condensed',sans-serif!important;letter-spacing:.01em;color:var(--text)!important}h1{font-size:3.15rem!important;line-height:.94!important;margin:.5rem 0 .25rem!important;text-transform:uppercase}h2{font-size:1.65rem!important;text-transform:uppercase}.subhead{color:var(--muted);font-size:1rem;margin-bottom:1rem}.status-label{font-size:.72rem;text-align:right;padding-top:.55rem}.status-LIVE{color:var(--red)}.status-PROVVISORIO{color:var(--amber)}.status-DEFINITIVO{color:var(--lime)}.status-NESSUN{color:var(--muted)}
        .section-label{color:#dfe8eb;font:700 1rem 'Barlow Condensed',sans-serif;text-transform:uppercase;letter-spacing:.04em;margin-bottom:.65rem}.panel{background:var(--navy2);border:1px solid var(--line);border-radius:10px;padding:1rem 1.15rem}.metric-strip{background:var(--navy2);border:1px solid var(--line);border-radius:10px;padding:.35rem .65rem;margin:.6rem 0 1rem}div[data-testid="stMetric"]{padding:.55rem .7rem}div[data-testid="stMetric"] label{color:#ced9dd!important;font:700 .84rem 'Barlow Condensed',sans-serif;text-transform:uppercase}div[data-testid="stMetricValue"]{font:700 2.15rem 'Barlow Condensed',sans-serif;color:var(--lime)}
        .pitch{position:relative;min-height:500px;border-radius:9px;overflow:hidden;border:1px solid #4d7c5b;background:linear-gradient(rgba(4,24,17,.18),rgba(4,24,17,.18)),repeating-linear-gradient(90deg,#123f2a 0,#123f2a 12.5%,#174b31 12.5%,#174b31 25%)}.pitch:before{content:"";position:absolute;inset:5%;border:2px solid rgba(225,245,226,.45)}.pitch:after{content:"";position:absolute;left:50%;top:5%;bottom:5%;width:2px;background:rgba(225,245,226,.35)}.center-circle{position:absolute;z-index:1;width:96px;height:96px;border:2px solid rgba(225,245,226,.4);border-radius:50%;left:50%;top:50%;transform:translate(-50%,-50%)}.player{position:absolute;z-index:3;transform:translate(-50%,-50%);min-width:104px;text-align:center;background:#071a27;border:1px solid #315066;border-radius:7px;padding:.34rem .45rem;box-shadow:0 5px 18px rgba(0,0,0,.22)}.player b{display:block;color:white;font:700 .72rem 'Inter',sans-serif;white-space:nowrap}.player span{color:var(--lime);font:700 .86rem 'Inter',sans-serif}
        .priority{border-left:3px solid var(--lime);background:#0a1f2e;padding:.85rem .8rem;margin:.45rem 0;border-radius:0 8px 8px 0}.priority.amber{border-color:var(--amber)}.priority.red{border-color:var(--red)}.priority .number{float:left;width:28px;height:28px;display:grid;place-items:center;margin-right:.7rem;border-radius:50%;background:var(--lime);color:#071722;font-weight:800}.priority.amber .number{background:var(--amber)}.priority.red .number{background:var(--red)}.priority b{color:var(--lime);font-size:.9rem}.priority.amber b{color:var(--amber)}.priority.red b{color:var(--red)}.priority p{color:#bccbd1;font-size:.78rem;margin:.25rem 0 .1rem 2.45rem;line-height:1.4}
        .decision-box{border:1px solid #315367;background:#0b2231;border-radius:10px;padding:1rem;min-height:140px}.decision-box h3{font-size:1.35rem!important;margin:0 0 .35rem}.decision-box p{color:#aabac1;font-size:.85rem}.verdict{color:var(--lime);font-weight:800;text-transform:uppercase}.positive{color:var(--lime);font-weight:700}.negative{color:var(--red);font-weight:700}.risk-low{color:var(--lime)}.risk-mid{color:var(--amber)}.risk-high{color:var(--red)}
        .source-card{display:grid;grid-template-columns:1.2fr .75fr .75fr 1fr;gap:.6rem;align-items:center;background:#0a1f2e;border:1px solid var(--line);border-radius:9px;padding:.8rem 1rem;margin:.35rem 0}.source-card small{color:var(--muted)}.audit{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.72rem;color:#9eb0b8;word-break:break-all}.source-note{color:#718c99;font-size:.72rem;margin-top:.6rem}
        .stButton>button,.stFormSubmitButton>button,.stDownloadButton>button{background:var(--lime);color:#071722;border:0;border-radius:7px;font-weight:800;text-transform:uppercase;min-height:2.7rem}.stButton>button:hover,.stFormSubmitButton>button:hover,.stDownloadButton>button:hover{background:#ddff31;color:#071722}div[data-baseweb="select"]>div{background:#0b2231!important;border-color:#315367!important;color:white!important}div[data-baseweb="select"] span{color:white!important}.stTextInput input,.stNumberInput input,.stTextArea textarea{background:#0b2231!important;border-color:#315367!important;color:white!important}[data-testid="stDataFrame"]{border:1px solid var(--line);border-radius:8px;overflow:hidden}[data-testid="stChatMessage"]{background:#081d2a;border:1px solid #203c4d;border-radius:10px;padding:.6rem 1rem}[data-testid="stChatMessage"] p,[data-testid="stChatMessage"] td,[data-testid="stChatMessage"] th{color:#dbe5e8!important}[data-testid="stChatMessage"] table{border-color:#315367!important}[data-testid="stBottomBlockContainer"],[data-testid="stBottomBlockContainer"]>div{background:var(--navy)!important}[data-testid="stChatInput"],[data-testid="stChatInput"]>div,[data-testid="stChatInput"] div[data-baseweb="base-input"]{background:#0b2231!important;border-color:#315367!important}[data-testid="stChatInput"] textarea{color:white!important;background:#0b2231!important}[data-testid="stChatInput"] textarea::placeholder{color:#91a4ae!important;opacity:1}[data-testid="stChatInput"] button{background:#142b39!important;color:#dbe5e8!important}
        @media(max-width:900px){[data-testid="stMainBlockContainer"]{padding:4rem 1rem 1rem}h1{font-size:2.3rem!important}.pitch{min-height:440px}.player{min-width:76px;padding:.28rem}.player b{font-size:.58rem}.source-card{grid-template-columns:1fr 1fr}.status-label{text-align:left}}
        .player{box-sizing:border-box;min-width:0;width:14%;max-width:104px;padding:.34rem .2rem}
        .player b{overflow:hidden;text-overflow:ellipsis}
        @media(max-width:1000px){[data-testid="stHorizontalBlock"]{flex-wrap:wrap!important}[data-testid="stHorizontalBlock"]>[data-testid="stColumn"]{flex:1 1 200px!important;min-width:0!important}[data-testid="stMetricLabel"] p{white-space:normal!important}}
        @media(max-width:1000px){[data-testid="stHorizontalBlock"]:has(.pitch){flex-wrap:wrap!important}[data-testid="stHorizontalBlock"]:has(.pitch)>[data-testid="stColumn"]{flex:1 1 100%!important;width:100%!important}}
        </style>
        """,
        unsafe_allow_html=True,
    )


def fmt_time(value: str | None) -> str:
    if not value:
        return "Mai"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(ZoneInfo("Europe/Rome")).strftime("%d/%m/%Y %H:%M %Z")
    except ValueError:
        return value


def import_source(db: Database, league: dict, matchday: int, payload: bytes, filename: str, content_type: str, source_url: str, status: str) -> dict:
    return import_votes(db, league, matchday, payload, filename, content_type,
                        source_url=source_url, default_status=status)


@st.fragment(run_every="30s")
def auto_sync(db: Database, league: dict, latest: dict | None) -> None:
    league = db.league(int(league["id"]))
    latest = db.latest_sync(int(league["id"]), int(league["matchday"]))
    if not sync_due(league, latest):
        return
    result = refresh_votes(db, league, int(league["matchday"]))
    if result["ok"]:
        st.toast("Feed voti aggiornato", icon="↻")
    st.rerun()


def status_label(latest: dict | None) -> tuple[str, str]:
    if not latest:
        return "NESSUN DATO", "NESSUN"
    status = str(latest.get("status") or "NESSUN DATO")
    if status == "ERRORE":
        return "ERRORE SYNC", "LIVE"
    return status, status


def top_controls(db: Database, league_id: int, latest: dict | None) -> tuple[dict, int]:
    leagues = db.leagues()
    ids = [int(item["id"]) for item in leagues]
    current_index = ids.index(league_id) if league_id in ids else 0
    c1, c2, c4 = st.columns([1.3, 1, 1.3])
    with c1:
        selected = st.selectbox("Lega", ids, index=current_index, format_func=lambda value: next(x["name"] for x in leagues if x["id"] == value), label_visibility="collapsed")
        if selected != league_id:
            st.session_state["league_id"] = selected
            st.rerun()
    league = db.league(selected)
    with c2:
        matchday = st.selectbox("Giornata", list(range(1, 39)), index=max(0, int(league["matchday"]) - 1), format_func=lambda value: f"Giornata {value}", label_visibility="collapsed")
    latest = db.latest_sync(int(league["id"]), matchday)
    label, css = status_label(latest)
    with c4:
        st.markdown(f'<div class="status-label status-{css}">● {html.escape(label)}<br><span style="color:#718c99">{fmt_time(latest.get("checked_at") if latest else None)}</span></div>', unsafe_allow_html=True)
    return league, matchday


def pitch_html(selected: list[dict]) -> str:
    role_levels = {"POR": 12, "DIF": 32, "CEN": 57, "ATT": 83}
    tags: list[str] = []
    for role in ("POR", "DIF", "CEN", "ATT"):
        group = [p for p in selected if p["role"] == role]
        for index, player in enumerate(group, start=1):
            left = index * 100 / (len(group) + 1)
            top = role_levels[role]
            tags.append(
                f'<div class="player" style="left:{left:.1f}%;top:{top}%"><b>{html.escape(str(player["name"]))}</b><span>{float(player["expected"]):.1f}</span></div>'
            )
    return f'<div class="pitch"><div class="center-circle"></div>{"".join(tags)}</div>'


def priority_html(roster: list[dict]) -> str:
    if not roster:
        return '<div class="panel">Aggiungi i tuoi giocatori nella sezione Rosa per iniziare.</div>'
    high_risk = sorted((p for p in roster if p["risk"] == "Alto"), key=lambda p: p["price"], reverse=True)
    value = sorted(roster, key=lambda p: float(p["expected"]) / max(int(p["price"]), 1), reverse=True)
    weak_role = min(("POR", "DIF", "CEN", "ATT"), key=lambda role: len([p for p in roster if p["role"] == role]))
    first = value[0] if value else {"name": "N/D", "price": 0}
    risky = high_risk[0] if high_risk else sorted(roster, key=lambda p: p["start_probability"])[0]
    return (
        f'<div class="priority"><span class="number">1</span><b>Proteggi il value di {html.escape(first["name"])}</b><p>Rendimento atteso elevato rispetto a {first["price"]} crediti di valore.</p></div>'
        f'<div class="priority amber"><span class="number">2</span><b>Monitora {html.escape(risky["name"])}</b><p>Titolarità {risky["start_probability"]}% e rischio {str(risky["risk"]).lower()}.</p></div>'
        f'<div class="priority red"><span class="number">3</span><b>Copri il reparto {weak_role}</b><p>È il reparto con minore profondità nella rosa salvata.</p></div>'
    )


def navigate(page: str) -> None:
    # Widget callbacks run before the next script render, so the navigation radio
    # has not yet been instantiated when its state is updated.
    st.session_state["page"] = page


def page_overview(db: Database, league: dict, matchday: int, latest: dict | None) -> None:
    roster = db.roster(int(league["id"]))
    formation, selected, expected = optimize_lineup(roster)
    records = db.records(int(league["id"]), matchday)
    title, action = st.columns([3.2, 1.15], vertical_alignment="center")
    with title:
        st.title("Il tuo vantaggio, questa giornata")
        st.markdown('<div class="subhead">Decisioni calcolate sulla tua lega, sulla tua rosa e sulla fonte voti configurata.</div>', unsafe_allow_html=True)
    with action:
        st.button("◎ Ottimizza formazione", width="stretch", type="primary",
                  on_click=navigate, args=("▥  Formazione",))
    saved = db.saved_lineup(int(league["id"]), matchday)
    tally = lineup_score(saved["players"] if saved else [], records,
                         saved.get("bench", []) if saved else [], int(league.get("max_substitutions", 3)), league)
    confidence = round(sum(int(p["start_probability"]) for p in selected) / max(len(selected), 1))
    risk_count = len([p for p in selected if p["risk"] == "Alto"])
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Titolarità media stimata", f"{confidence}%")
    m2.metric("Fantapunti attesi", f"{expected:.1f}")
    m3.metric("Punti formazione" if tally["complete"] else "Punti parziali formazione", f"{tally['total']:.1f}" if tally["total"] is not None else "—")
    m4.metric("Rischio undici", "ALTO" if risk_count >= 3 else "MEDIO" if risk_count else "BASSO")
    if saved:
        detail = f"Voti disponibili: {tally['count']}/11 · sostituzioni applicate: {len(tally['substitutions'])}/{league.get('max_substitutions', 3)}."
        if tally["substitutions"]:
            detail += " " + "; ".join(f"{item['out']} → {item['in']}" for item in tally["substitutions"])
        if tally["defense_modifier"]:
            detail += f" Modificatore difesa: +{tally['defense_modifier']:g} (media {tally['defense_average']:.2f})."
        st.caption(detail)
    else:
        st.caption("Salva la formazione della giornata per seguirne il punteggio.")
    if league["mode"] != "Classic":
        st.warning("Ottimizzazione Classic: i ruoli e i moduli Mantra non sono ancora supportati.")
        return
    left, right = st.columns([2.15, 1], gap="small")
    with left:
        st.markdown(f'<div class="section-label">Formazione consigliata &nbsp; <span style="color:#43c7dc">{formation}</span></div>', unsafe_allow_html=True)
        if selected:
            st.markdown(pitch_html(selected), unsafe_allow_html=True)
        else:
            st.warning("La rosa non contiene abbastanza giocatori per un modulo valido.")
    with right:
        st.markdown('<div class="section-label">Decisioni prioritarie</div>', unsafe_allow_html=True)
        st.markdown(priority_html(roster), unsafe_allow_html=True)
        st.markdown("##### Stato della fonte")
        if latest:
            st.progress(min(1.0, float(latest.get("rows_received", 0)) / max(len(roster), 1)), text=f"{latest['rows_received']} record · {latest['status']}")
            st.caption(f"{latest['source_name']} · verificato {fmt_time(latest['checked_at'])}")
        else:
            st.info("Importa il primo file ufficiale nella sezione Voti & dati.")
    st.markdown("### Segnali della rosa")
    if not roster:
        st.info("La rosa è vuota. Apri Rosa per aggiungere giocatori o importare un CSV.")
        return
    a, b = st.columns(2)
    undervalued = sorted(roster, key=lambda p: (p["trend"], p["expected"] / max(p["price"], 1)), reverse=True)[:3]
    risks = sorted(roster, key=lambda p: (p["risk"] == "Alto", -p["start_probability"]), reverse=True)[:3]
    with a:
        st.dataframe(pd.DataFrame(undervalued)[["name", "role", "price", "trend"]].rename(columns={"name":"Giocatore","role":"Ruolo","price":"Valore","trend":"Trend %"}), hide_index=True, width="stretch")
    with b:
        st.dataframe(pd.DataFrame(risks)[["name", "role", "risk", "start_probability"]].rename(columns={"name":"Giocatore","role":"Ruolo","risk":"Rischio","start_probability":"Titolarità %"}), hide_index=True, width="stretch")


def page_lineup(db: Database, league: dict, matchday: int) -> None:
    roster = db.roster(int(league["id"]))
    st.title("Ottimizzatore formazione")
    if league["mode"] != "Classic":
        st.warning("I moduli Mantra richiedono un motore dedicato. L'ottimizzatore attuale supporta Classic.")
        return
    st.caption(f"{league['season']} · Giornata {matchday} · Stime inserite in rosa, non previsioni live.")
    st.markdown('<div class="subhead">Il motore confronta tutti i moduli validi pesando rendimento, titolarità, trend e rischio.</div>', unsafe_allow_html=True)
    controls, result = st.columns([1, 2], gap="large")
    with controls:
        strategy = st.radio("Profilo strategico", ["Equilibrato", "Difendi il vantaggio", "Cerca il bonus"])
        formation, selected, total = optimize_lineup(roster, strategy)
        bench = optimize_bench(roster, selected, int(league.get("bench_size", 7)), strategy)
        st.markdown(f'<div class="decision-box"><h3>Verdetto</h3><div class="verdict">{formation} · {strategy}</div><p>{total:.1f} fantapunti attesi. Il risultato viene ricalcolato dai dati persistiti della rosa.</p></div>', unsafe_allow_html=True)
        if st.button("Conferma undici", width="stretch", disabled=len(selected) != 11):
            db.save_lineup(int(league["id"]), matchday, formation, [p["id"] for p in selected], [p["id"] for p in bench])
            st.success(f"Formazione {formation} e panchina salvate per la giornata {matchday}.")
    with result:
        if selected:
            st.markdown(pitch_html(selected), unsafe_allow_html=True)
        else:
            st.warning("Completa la rosa per generare un undici valido.")
        if bench:
            st.caption("Panchina consigliata, in ordine: " + " · ".join(f"{p['name']} ({p['role']})" for p in bench))
    st.markdown("### Graduatoria per ruolo")
    ranking = sorted(roster, key=lambda p: player_score(p, strategy), reverse=True)
    rank_df = pd.DataFrame([{"Giocatore": p["name"], "Ruolo": p["role"], "Score": round(player_score(p, strategy), 2), "FP attesi": p["expected"], "Titolarità %": p["start_probability"], "Rischio": p["risk"]} for p in ranking])
    st.dataframe(rank_df, hide_index=True, width="stretch", column_config={"Titolarità %": st.column_config.ProgressColumn(min_value=0,max_value=100,format="%d%%")})
    with st.expander("Scegli manualmente i titolari"):
        with st.form(f"manual_lineup_{league['id']}_{matchday}"):
            manual_formation = st.selectbox("Modulo", list(FORMATION_LIMITS))
            by_id = {p["id"]: p for p in roster}
            ids = st.multiselect("Undici titolari", list(by_id), max_selections=11,
                                 format_func=lambda pid: f"{by_id[pid]['name']} · {by_id[pid]['role']}")
            bench_ids = st.multiselect("Panchina in ordine di ingresso", [pid for pid in by_id if pid not in ids],
                                       max_selections=int(league.get("bench_size", 7)),
                                       format_func=lambda pid: f"{by_id[pid]['name']} · {by_id[pid]['role']}")
            if st.form_submit_button("Salva formazione manuale"):
                try:
                    db.save_lineup(int(league["id"]), matchday, manual_formation, ids, bench_ids)
                    st.success("Formazione manuale salvata.")
                except ValueError as exc:
                    st.error(str(exc))
    saved = db.saved_lineup(int(league["id"]), matchday)
    if saved:
        st.markdown(f"### Formazione salvata · {saved['formation']}")
        st.caption(f"Salvata il {fmt_time(saved['saved_at'])}. È una copia dell'undici al momento della conferma.")
        st.write(", ".join(p["name"] for p in saved["players"]))
        if saved.get("bench"):
            st.caption("Panchina: " + " · ".join(f"{p['name']} ({p['role']})" for p in saved["bench"]))
        tally = lineup_score(saved["players"], db.records(int(league["id"]), matchday),
                             saved.get("bench", []), int(league.get("max_substitutions", 3)), league)
        st.write(f"Voti disponibili: {tally['count']}/11 · Punti: {tally['total'] if tally['total'] is not None else '—'}")
        if tally["substitutions"]:
            st.success("Sostituzioni: " + "; ".join(f"{item['out']} → {item['in']}" for item in tally["substitutions"]))
        if tally["defense_average"] is not None:
            st.info(f"Modificatore difesa: media {tally['defense_average']:.2f} · bonus {tally['defense_modifier']:+g}")
        if tally["missing"]:
            st.caption("Senza punteggio abbinabile: " + ", ".join(tally["missing"]))
        st.download_button("Esporta formazione", json.dumps(saved, ensure_ascii=False, indent=2),
                           f"formazione-{league['season']}-{matchday}.json", "application/json")


def page_auction(db: Database, league: dict) -> None:
    roster = db.roster(int(league["id"]))
    statistics = db.complete_player_catalog(int(league["id"]))
    st.title("Auction war room")
    st.caption("Acquisti registrati nella rosa e nel registro movimenti. Il prezzo obiettivo resta una tua valutazione.")
    if "auction_feedback" in st.session_state:
        st.success(st.session_state.pop("auction_feedback"))
    candidates = [p for p in statistics if not any(possible_duplicate(p, owned) for owned in roster)]
    if not candidates:
        st.info("Carica le rose complete Diretta.it per mostrare tutti i calciatori della Serie A.")
        if st.button("Carica rose Serie A", key="auction_sync_squads"):
            result = sync_diretta_rosters(db, league["season"])
            if result["ok"]:
                st.session_state["auction_feedback"] = f"Rose aggiornate: {result['players']} calciatori in {result['teams']} squadre."
                st.rerun()
            st.error(result["error"])
        return
    st.markdown('<div class="subhead">Stop loss calcolato sul budget reale residuo, sugli slot mancanti e sul rendimento ufficiale osservato.</div>', unsafe_allow_html=True)
    left, right = st.columns([1, 1.55], gap="large")
    residual = max(0, db.available_budget(int(league["id"])))
    with left:
        with st.form("auction"):
            candidate_keys = list(range(len(candidates)))
            candidate_index = st.selectbox("Giocatore sul tavolo", candidate_keys,
                format_func=lambda index: f"{candidates[index]['name']} · {candidates[index]['role']} · {candidates[index]['team']}")
            target = st.number_input("Valore obiettivo", min_value=1, max_value=int(league["budget"]), value=10)
            slots = st.number_input("Slot ancora da acquistare", min_value=1, max_value=100, value=max(1, 25-len(roster)))
            current_bid = st.number_input("Offerta attuale", min_value=0, max_value=int(league["budget"]), value=1)
            counterparty = st.text_input("Controparte (facoltativa)", placeholder="Nome fantallenatore")
            submitted = st.form_submit_button("Registra acquisto", width="stretch", type="primary")
        player = candidates[candidate_index]
        role_count = len([p for p in roster if p["role"] == player["role"]])
        scarcity = 1.12 if player["role"] == "ATT" or role_count < 4 else 1.0
        reserve = max(int(slots) - 1, 0)
        max_bid = max(0, min(round(int(target) * scarcity), residual - reserve))
        verdict = "RILANCIA" if current_bid < max_bid else "LASCIALO"
        st.markdown(f'<div class="decision-box"><h3>Verdetto live</h3><div class="verdict">{verdict}</div><p>Budget residuo <b>{residual} cr</b> · stop loss <b>{max_bid} cr</b> · riserva minima {reserve} cr.</p></div>', unsafe_allow_html=True)
        if submitted:
            try:
                db.acquire_player(int(league["id"]), {**player, "expected": player.get("average_fantavote") or 6,
                    "start_probability": 75, "risk": "Medio", "price": int(target), "tier": "C", "trend": player.get("trend", 0)},
                    int(current_bid), counterparty=counterparty)
                st.session_state["auction_feedback"] = f"{player['name']} acquistato per {current_bid} crediti."
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    with right:
        st.markdown("### Catalogo Serie A")
        board = pd.DataFrame([{"Giocatore":p["name"],"Ruolo":p["role"],"FM lega":p["average_fantavote"],"Presenze":p["appearances"],
            "MV":p["average_vote"],"Squadra":p["team"],"Gol":p["goals"],"Assist":p["assists"],"Trend %":p["trend"]} for p in candidates])
        st.dataframe(board, hide_index=True, width="stretch")
        st.caption("Rose e ruoli: Diretta.it. Medie e trend, quando presenti, provengono solo dalle giornate Gazzetta importate. Non sono previsioni di titolarità né quotazioni d'asta.")


def page_roster(db: Database, league: dict, matchday: int) -> None:
    roster = db.roster(int(league["id"]))
    st.title("Rosa & rischio")
    st.caption("Aggiungi o rimuovi righe, poi salva. FP attesi, titolarità, rischio e valore sono tue stime: il download dei voti non le aggiorna.")
    if "roster_feedback" in st.session_state:
        st.success(st.session_state.pop("roster_feedback"))
    fields = [*PLAYER_FIELDS, "purchase_cost"]
    columns = {
        "vote_provider": None,
        "provider_player_id": None,
        "name": st.column_config.TextColumn("Giocatore", required=True),
        "role": st.column_config.SelectboxColumn("Ruolo", options=["POR", "DIF", "CEN", "ATT"], required=True),
        "team": st.column_config.TextColumn("Squadra", default=""),
        "expected": st.column_config.NumberColumn("FP attesi", min_value=0, max_value=30, default=6.0),
        "start_probability": st.column_config.NumberColumn("Titolarità %", min_value=0, max_value=100, default=75, step=1),
        "risk": st.column_config.SelectboxColumn("Rischio", options=["Basso", "Medio", "Alto"], default="Medio"),
        "price": st.column_config.NumberColumn("Valore cr", min_value=0, max_value=5000, default=1, step=1),
        "tier": st.column_config.SelectboxColumn("Tier", options=["S", "A", "B", "C", "D", "E"], default="C"),
        "trend": st.column_config.NumberColumn("Trend %", min_value=-100, max_value=100, default=0, step=1),
        "purchase_cost": st.column_config.NumberColumn("Costo", min_value=0, max_value=5000, default=0, step=1),
    }
    frame = pd.DataFrame(roster, columns=fields)
    editor_version = st.session_state.get("roster_version", 0)
    edited = st.data_editor(frame, hide_index=True, width="stretch", num_rows="dynamic", column_config=columns,
                            key=f"roster_editor_{league['id']}_{editor_version}")
    if st.button("Salva rosa", type="primary"):
        try:
            rows = [{key: value for key, value in row.items() if pd.notna(value)} for row in edited.to_dict("records")]
            db.replace_roster(int(league["id"]), rows)
            st.session_state["roster_feedback"] = "Rosa salvata. Le formazioni già confermate restano nello storico."
            st.session_state["roster_version"] = editor_version + 1
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    c1, c2, c3 = st.columns(3)
    spent = sum(p["purchase_cost"] for p in roster)
    c1.metric("Giocatori", len(roster))
    c2.metric("Costo rosa", f"{spent} cr")
    available = db.available_budget(int(league["id"]))
    c3.metric("Budget residuo", f"{available} cr")
    if available < 0:
        st.warning("Il costo della rosa supera il budget. Controlla i costi d'acquisto.")
    statistics = db.season_statistics(int(league["id"]))
    if statistics and roster:
        by_source = {(row.get("vote_provider"), row.get("provider_player_id")): row for row in statistics if row.get("provider_player_id")}
        by_name = {(row["name"].casefold(), row.get("team", "").casefold()): row for row in statistics}
        observed = []
        for player in roster:
            stat = by_source.get((player.get("vote_provider"), player.get("provider_player_id")))
            stat = stat or by_name.get((player["name"].casefold(), player.get("team", "").casefold()))
            if stat:
                observed.append({"Giocatore": player["name"], "Ruolo": player["role"], "Presenze": stat["appearances"],
                    "Media voto": stat["average_vote"], "Fantamedia lega": stat["average_fantavote"],
                    "Gol": stat["goals"], "Assist": stat["assists"], "Trend ultime 3 %": stat["trend"]})
        if observed:
            with st.expander("Rendimento ufficiale della rosa", expanded=True):
                st.dataframe(pd.DataFrame(observed), hide_index=True, width="stretch")
                st.caption("Calcolato dalle giornate importate per questa stagione e redazione; separato dalle stime usate dall'ottimizzatore.")
    if roster:
        with st.expander("Registra una cessione"):
            with st.form("release_player"):
                by_id = {p["id"]: p for p in roster}
                player_id = st.selectbox("Giocatore ceduto", list(by_id), format_func=lambda value: f"{by_id[value]['name']} · {by_id[value]['role']}")
                proceeds = st.number_input("Crediti recuperati", 0, 5000, 0)
                counterparty = st.text_input("Controparte", placeholder="Svincolo o nome fantallenatore")
                note = st.text_input("Nota", placeholder="Facoltativa")
                if st.form_submit_button("Conferma cessione"):
                    try:
                        name = by_id[player_id]["name"]
                        db.release_player(int(league["id"]), int(player_id), int(proceeds), counterparty=counterparty, note=note)
                        st.session_state["roster_feedback"] = f"Cessione di {name} registrata."
                        st.session_state["roster_version"] = editor_version + 1
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
    transactions = db.transactions(int(league["id"]))
    if transactions:
        with st.expander("Registro movimenti"):
            movement_df = pd.DataFrame(transactions).rename(columns={"kind":"Tipo","player_name":"Giocatore","role":"Ruolo",
                "team":"Squadra","amount":"Crediti","counterparty":"Controparte","note":"Nota","created_at":"Data"})
            st.dataframe(movement_df[["Data","Tipo","Giocatore","Ruolo","Squadra","Crediti","Controparte","Nota"]], hide_index=True, width="stretch")
    st.download_button("Esporta rosa CSV", roster_csv(roster), "rosa-fantaoperator.csv", "text/csv")
    with st.expander("Importa la tua rosa da CSV"):
        st.caption("Usa il modello: name, role (POR/DIF/CEN/ATT), team e purchase_cost. Le altre colonne sono stime facoltative; valori iniziali neutri: 6 FP, 75% titolarità e rischio medio.")
        st.download_button("Scarica modello rosa", roster_csv([]), "modello-rosa.csv", "text/csv")
        upload = st.file_uploader("CSV rosa", type=["csv"], key="roster_upload")
        if upload:
            try:
                imported = parse_roster_csv(upload.getvalue())
                st.dataframe(pd.DataFrame(imported), hide_index=True)
                st.caption("L'import sostituisce la rosa attuale. Scarica prima l'export per conservarla.")
                if st.button("Importa questa rosa", disabled=not imported):
                    db.replace_roster(int(league["id"]), imported)
                    st.session_state["roster_feedback"] = f"Importati {len(imported)} giocatori."
                    st.session_state["roster_version"] = editor_version + 1
                    st.rerun()
            except (ValueError, UnicodeError) as exc:
                st.error(str(exc))
    with st.expander("Aggiungi un giocatore dal catalogo Serie A"):
        records = db.complete_player_catalog(int(league["id"]))
        owned = {p["name"].casefold() for p in roster}
        candidates = [r for r in records if not any(possible_duplicate(r, player) for player in roster)]
        if candidates:
            teams = sorted({r["team"] for r in candidates}, key=str.casefold)
            selected_team = st.selectbox("Squadra", teams)
            team_candidates = [r for r in candidates if r["team"] == selected_team]
            names = {f"{r['name']} · {r['role']} · {r['team']}": r for r in team_candidates}
            chosen = st.selectbox("Giocatore disponibile", list(names))
            cost = st.number_input("Costo d'acquisto", 0, 5000, 1)
            if st.button("Aggiungi alla rosa"):
                row = names[chosen]
                display_name = row["name"] if row["name"].casefold() not in owned else f"{row['name']} ({row['team']})"
                db.replace_roster(int(league["id"]), [*roster, {"name": display_name, "role": row["role"], "team": row["team"], "purchase_cost": cost,
                    "vote_provider": row.get("vote_provider", "") if row.get("provider_player_id") else "", "provider_player_id": row.get("provider_player_id", "")}])
                st.session_state["roster_feedback"] = "Giocatore aggiunto. Completa le tue stime nella tabella."
                st.session_state["roster_version"] = editor_version + 1
                st.rerun()
        else:
            st.info("Carica le rose complete Diretta.it per avere tutti i nomi e i ruoli della Serie A.")
            if st.button("Carica rose Serie A", key="roster_sync_squads"):
                result = sync_diretta_rosters(db, league["season"])
                if result["ok"]:
                    st.session_state["roster_feedback"] = f"Rose aggiornate: {result['players']} calciatori in {result['teams']} squadre."
                    st.rerun()
                st.error(result["error"])


def page_market(db: Database, league: dict) -> None:
    roster = db.roster(int(league["id"]))
    statistics = db.complete_player_catalog(int(league["id"]))
    st.title("Mercato & scambi")
    if "market_feedback" in st.session_state:
        st.success(st.session_state.pop("market_feedback"))
    if not roster:
        st.info("Inserisci la tua rosa prima di valutare uno scambio.")
        return
    candidates = [p for p in statistics if not any(possible_duplicate(p, owned) for owned in roster)]
    if not candidates:
        st.info("Carica le rose complete Diretta.it per confrontare la rosa con tutti i calciatori esterni.")
        if st.button("Carica rose Serie A", key="market_sync_squads"):
            result = sync_diretta_rosters(db, league["season"])
            if result["ok"]:
                st.session_state["market_feedback"] = f"Rose aggiornate: {result['players']} calciatori in {result['teams']} squadre."
                st.rerun()
            st.error(result["error"])
        return
    st.caption("Confronto tra una tua stima e il rendimento ufficiale osservato del giocatore in entrata.")
    st.markdown('<div class="subhead">Se confermi, lo scambio aggiorna la rosa e il registro movimenti in un’unica operazione.</div>', unsafe_allow_html=True)
    a, arrow, b = st.columns([1,.2,1])
    with a: give=st.selectbox("Cedi",[p["name"] for p in roster],index=0)
    with arrow: st.markdown("<h2 style='text-align:center;margin-top:2rem'>⇄</h2>",unsafe_allow_html=True)
    with b:
        candidate_index=st.selectbox("Ricevi",list(range(len(candidates))),format_func=lambda index: f"{candidates[index]['name']} · {candidates[index]['role']} · {candidates[index]['team']}")
    left=next(p for p in roster if p["name"]==give)
    observed=candidates[candidate_index]
    right={**observed,"expected":observed.get("average_fantavote") or 6,"start_probability":75,"risk":"Medio",
           "price":left["price"],"tier":"C","trend":observed.get("trend",0)}
    result=compare_players(left,right)
    c1,c2,c3=st.columns(3);c1.metric("Delta score",f"{result['delta']:+.2f}");c2.metric("Fantamedia entrata",f"{observed['average_fantavote']:.2f}" if observed['average_fantavote'] is not None else "—");c3.metric("Verdetto",result["verdict"])
    st.markdown(f'<div class="decision-box"><h3>Azione</h3><div class="verdict">{result["verdict"]}: {html.escape(give)} → {html.escape(observed["name"])}</div><p>Indice euristico {result["confidence"]}/100. Per il giocatore in entrata usa la fantamedia delle giornate importate; per quello ceduto usa le stime della rosa.</p></div>',unsafe_allow_html=True)
    with st.form("confirm_exchange"):
        incoming_cost = st.number_input("Nuovo costo a bilancio", 0, 5000, int(left["purchase_cost"]))
        estimated = st.number_input("Stima FP per il nuovo giocatore", 0.0, 30.0, float(observed.get("average_fantavote") or 6), step=.1)
        counterparty = st.text_input("Controparte", placeholder="Nome fantallenatore")
        note = st.text_input("Nota", placeholder="Facoltativa")
        if st.form_submit_button("Conferma scambio", type="primary"):
            try:
                db.exchange_player(int(league["id"]), int(left["id"]), {**right, "expected": estimated}, int(incoming_cost),
                                   counterparty=counterparty, note=note)
                st.session_state["market_feedback"] = f"Scambio registrato: {give} → {observed['name']}."
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


def page_votes(db: Database, league: dict, matchday: int) -> None:
    st.title("Voti & dati ufficiali")
    st.markdown('<div class="subhead">La fonte voti configurata prevale sempre. Ogni import conserva provenienza, stato, timestamp e rettifiche.</div>', unsafe_allow_html=True)
    latest = db.latest_sync(int(league["id"]), matchday)
    st.caption(f"{league['season']} · Giornata {matchday} · {league['vote_provider']} / {league['vote_edition']}")
    if "import_feedback" in st.session_state:
        st.success(st.session_state.pop("import_feedback"))
    if "squad_feedback" in st.session_state:
        st.success(st.session_state.pop("squad_feedback"))
    if latest:
        label, css = status_label(latest)
        st.markdown(f'<div class="source-card"><div><b>{html.escape(str(latest["source_name"]))}</b><br><small>Provider dichiarato</small></div><div><b class="status-{css}">{label}</b><br><small>Stato dati</small></div><div><b>{latest["rows_received"]}</b><br><small>Record ricevuti</small></div><div><b>{fmt_time(latest["checked_at"])}</b><br><small>Ultimo tentativo / acquisizione</small></div></div>', unsafe_allow_html=True)
        if latest["provenance"] == "PAGINA_UFFICIALE":
            st.caption(f"Pagina pubblica {latest['source_name']} ricontrollata. Consolidamento non attestato: dati PROVVISORI. Nessuna garanzia di voti live durante le partite.")
        else:
            st.caption("Import locale: provenienza non verificata sul Web." if latest["provenance"] == "IMPORT_LOCALE" else "Feed configurato: metadati confrontati con la lega. Non è una certificazione indipendente del provider.")
        if latest.get("payload_hash"):
            st.markdown(f'<div class="audit">SHA-256 · {latest["payload_hash"]}</div>',unsafe_allow_html=True)
        if latest.get("error"):
            st.error(latest["error"])
    left,right=st.columns(2,gap="large")
    with left:
        st.markdown("### Importa export ufficiale")
        status=st.selectbox("Stato dei dati",["LIVE","PROVVISORIO","DEFINITIVO"],index=1)
        uploaded=st.file_uploader("XLSX, CSV o JSON del provider configurato",type=["xlsx","csv","json"])
        confirm_key=f"confirm_{league['id']}_{league['season']}_{league['vote_provider']}_{league['vote_edition']}_{matchday}_{uploaded.file_id if uploaded else 'none'}"
        confirmed=st.checkbox("Confermo fonte, redazione, stagione e giornata dell’export",key=confirm_key)
        st.caption("S.V. resta senza punteggio. Le colonne ambigue vengono rifiutate; nessun valore inventato.")
        if uploaded and st.button("Importa e ricalcola",type="primary",width="stretch",disabled=not confirmed):
            try:
                result=import_source(db,league,matchday,uploaded.getvalue(),uploaded.name,uploaded.type or "","",status)
                st.session_state["import_feedback"]=f"{result['rows']} record importati · {result['changed']} modifiche"
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
        st.download_button("Scarica modello CSV",csv_template(),"fantaoperator-voti-template.csv","text/csv",width="stretch")
    with right:
        st.markdown("### Sincronizza URL configurato")
        from fantaoperator.public_votes import PUBLIC_VOTES_URL, is_public_votes_url
        public_url = gazzetta_votes_url(league["season"]) if league["vote_provider"] == GAZZETTA_PROVIDER else PUBLIC_VOTES_URL
        if league["vote_provider"] in (GAZZETTA_PROVIDER, "Fantacalcio.it"):
            st.link_button(f"Apri la pagina voti {league['vote_provider']}", public_url, width="stretch")
        if not league.get("source_url") and league["vote_provider"] in (GAZZETTA_PROVIDER, "Fantacalcio.it"):
            if st.button("Collega questa fonte ufficiale", width="stretch"):
                db.save_league(int(league["id"]), {"source_url": public_url}, league["scoring"])
                st.rerun()
        st.code(safe_url(league["source_url"]) if league.get("source_url") else "Nessun feed collegato",language=None)
        if is_gazzetta_votes_url(league.get("source_url", "")):
            st.caption("Fonte Gazzetta: V è il voto ufficiale, FV Gazzetta è il fantavoto pubblicato. FV lega applica i bonus configurati e può differire. Clean sheet non esplicito; i giocatori senza voto restano senza punteggio calcolato.")
        elif is_public_votes_url(league.get("source_url", "")):
            st.caption("Lettura diretta della pagina pubblica per stagione, giornata e redazione selezionate, senza login. Bonus Redazione Fantacalcio; codici speciali senza FV lasciati senza punteggio. Clean sheet e Player of the match non calcolati.")
        else:
            st.caption("Il feed deve dichiarare provider, edition, season e matchday. Stato assente = PROVVISORIO. HTML generico e pagine di login vengono rifiutati.")
        if st.button("↻ Verifica e sincronizza",width="stretch",disabled=not bool(league.get("source_url"))):
            result=refresh_votes(db,league,matchday)
            if result["ok"]:
                st.session_state["import_feedback"]=f"Feed acquisito: {result['rows']} record · {result['changed']} modifiche"
                st.rerun()
            else:
                st.rerun()
        template={"schema_version":1,"provider":league["vote_provider"],"edition":league["vote_edition"],"season":league["season"],"matchday":matchday,
                  "records":[{"player":"Esempio Giocatore","vote":6.5,"status":"PROVVISORIO","goals":1,"penalties_scored":0,"penalties_missed":0}]}
        st.download_button("Scarica schema JSON collector",json.dumps(template,ensure_ascii=False,indent=2),"schema-voti.json","application/json",width="stretch")
        with st.expander("Aggiornamento dei voti"):
            interval = int(league.get("auto_sync_minutes") or 0)
            if not league.get("source_url"):
                st.info("Collega una fonte voti per attivare la sincronizzazione.")
            elif interval:
                st.write(f"Aggiornamento automatico ogni {interval} minuti mentre la sessione è aperta.")
                st.caption("L’app controlla ogni 30 secondi se è il momento di aggiornare i voti.")
            else:
                st.write("Aggiornamento automatico disattivato. Usa «Verifica e sincronizza» per scaricare i voti.")
            st.caption("Puoi scegliere l’intervallo in Impostazioni → Aggiornamento automatico.")
            if not os.getenv("FANTAOPERATOR_DB"):
                st.info("Questa sessione non dispone di aggiornamenti a browser chiuso. Scarica il backup in Impostazioni prima di uscire per conservare rosa e formazioni.")
            else:
                st.caption("Per aggiornare anche a browser chiuso, l’installazione locale richiede un processo separato sullo stesso database. Le istruzioni sono nel README del progetto.")
        with st.expander("Importare la rosa della tua lega"):
            st.write("Puoi caricare la rosa da un file CSV nella sezione Rosa oppure scegliere i giocatori dal catalogo Serie A.")
            st.caption("L’importazione diretta dall’area privata della piattaforma di lega non è ancora disponibile.")
    st.markdown("### Rose complete Serie A")
    squad_sync = db.latest_squad_sync(league["season"], DIRETTA_PROVIDER)
    squad_left, squad_right = st.columns([2, 1])
    with squad_left:
        st.caption("Diretta.it fornisce squadre, nomi completi e ruoli per asta e mercato. Gazzetta resta l'unica fonte dei voti ufficiali.")
        st.link_button("Apri le rose su Diretta.it", DIRETTA_ROSTERS_URL, width="stretch")
    with squad_right:
        if squad_sync and squad_sync["status"] == "OK":
            st.metric("Calciatori nel catalogo", squad_sync["players"])
            st.caption(f"{squad_sync['teams']} squadre · articolo aggiornato {fmt_time(squad_sync['article_updated_at'])}")
        elif squad_sync:
            st.error(squad_sync["error"])
        else:
            st.info("Catalogo non ancora caricato")
        if st.button("↻ Aggiorna rose Diretta.it", width="stretch"):
            result = sync_diretta_rosters(db, league["season"])
            if result["ok"]:
                duplicate_note = f" · {len(result['warnings'])} duplicati rimossi" if result["warnings"] else ""
                st.session_state["squad_feedback"] = f"Catalogo aggiornato: {result['players']} calciatori, {result['teams']} squadre{duplicate_note}."
                st.rerun()
            st.error(result["error"])
    records=db.records(int(league["id"]),matchday)
    st.markdown(f"### Giornata {matchday}")
    if records:
        if any(row["fantavote"] is None for row in records):
            st.warning("Punteggi incompleti: S.V., codici speciali senza FV o bonus clean sheet non verificabile. Le sostituzioni Classic e il modificatore difesa configurato sono applicati alla formazione salvata; altri voti d’ufficio o modificatori richiedono regole dedicate.")
        if latest and (latest["status"] == "ERRORE" or any(row["checked_at"] != latest["checked_at"] for row in records)):
            st.warning("Alcuni dati appartengono a verifiche precedenti. Controlla il timestamp di ogni riga.")
        df=pd.DataFrame(records).rename(columns={"name":"Giocatore","role":"Ruolo","team":"Squadra","official_vote":"Voto","fantavote":"Fantavoto","status":"Stato","goals":"Gol","assists":"Assist","yellow_cards":"Amm.","red_cards":"Esp.","source_name":"Fonte","checked_at":"Acquisito"})
        visible_columns = ["Giocatore","Ruolo","Squadra","Voto","Fantavoto","Gol","Assist","Amm.","Esp.","Stato","Fonte","Acquisito"]
        if "provider_fantavote" in df:
            df = df.rename(columns={"provider_fantavote":"FV Gazzetta", "Fantavoto":"FV lega"})
            visible_columns[4:5] = ["FV Gazzetta", "FV lega"]
        st.dataframe(df[visible_columns],hide_index=True,width="stretch")
    else:
        st.info("Nessun voto importato per questa giornata.")
    history=db.sync_history(int(league["id"]),matchday=matchday)
    if history:
        with st.expander("Registro verifiche e rettifiche"):
            audit=pd.DataFrame(history).rename(columns={"checked_at":"Timestamp","source_name":"Fonte","status":"Stato","rows_received":"Record","rows_changed":"Modifiche","error":"Errore","payload_hash":"SHA-256"})
            st.dataframe(audit[["Timestamp","Fonte","Stato","Record","Modifiche","Errore","SHA-256"]],hide_index=True,width="stretch")


def page_assistant(db: Database, league: dict, matchday: int) -> None:
    st.title("Assistente operativo")
    st.markdown('<div class="subhead">Motore locale: risponde sui dati persistiti senza inviare informazioni a servizi esterni.</div>',unsafe_allow_html=True)
    c1,c2=st.columns([4,1])
    with c2:
        if st.button("Pulisci chat",width="stretch"):
            db.clear_assistant(int(league["id"]));st.rerun()
    messages=db.assistant_messages(int(league["id"]))
    if not messages:
        st.info("Prova: /FORMAZIONE, /VOTI, /VOTILIVE, /AGGIORNAVOTI oppure confronta due giocatori della rosa.")
    for message in messages:
        with st.chat_message(message["role"]): st.markdown(message["content"])
    prompt=st.chat_input("Chiedi una decisione operativa…")
    if prompt:
        db.add_assistant_message(int(league["id"]),"user",prompt)
        with st.spinner("Verifica richiesta e dati della lega…"):
            response=respond(db,league,matchday,prompt)
        db.add_assistant_message(int(league["id"]),"assistant",response)
        st.rerun()


def page_settings(db: Database, league: dict) -> None:
    st.title("Impostazioni lega")
    with st.expander("Backup e ripristino", expanded=not bool(os.getenv("FANTAOPERATOR_DB"))):
        st.caption("Il backup conserva configurazione, rosa, formazioni con panchina, movimenti e chat. I voti non sono inclusi perché possono essere sincronizzati di nuovo. Gli URL privati e l'aggiornamento automatico non vengono ripristinati.")
        st.download_button("Scarica backup personale", db.export_workspace(int(league["id"])),
                           "fantaoperator-backup.json", "application/json")
        backup = st.file_uploader("Ripristina backup personale", type=["json"], key="workspace_backup")
        if backup:
            st.caption("Il ripristino sostituisce configurazione, rosa e formazioni attuali. Scarica prima il backup per conservarle.")
            if st.button("Ripristina questo backup"):
                try:
                    db.restore_workspace(int(league["id"]), backup.getvalue())
                    # Widget values must be recreated from the restored data.
                    keep = {k: v for k, v in st.session_state.items() if k in ("workspace_db", "workspace_directory")}
                    st.session_state.clear()
                    st.session_state.update(keep)
                    st.rerun()
                except (ValueError, UnicodeError) as exc:
                    st.error(str(exc))
    st.markdown('<div class="subhead">Questi vincoli alimentano tutti i calcoli e restano persistenti tra un avvio e l’altro.</div>',unsafe_allow_html=True)
    rules=league["scoring"]
    with st.form("league_settings"):
        st.markdown("### Contesto")
        a,b,c=st.columns(3)
        name=a.text_input("Nome lega",league["name"]);platform=b.selectbox("Piattaforma",["Fantacalcio.it","Leghe Fantacalcio","Gazzetta","Altro"],index=["Fantacalcio.it","Leghe Fantacalcio","Gazzetta","Altro"].index(league["platform"]) if league["platform"] in ["Fantacalcio.it","Leghe Fantacalcio","Gazzetta","Altro"] else 3);mode=c.selectbox("Modalità",["Classic"],index=0)
        if league["mode"] != "Classic":
            st.warning("Questo backup usa Mantra. Salva la configurazione per convertirlo in un workspace Classic prima di calcolare la formazione.")
        d,e,f=st.columns(3);participants=d.number_input("Partecipanti",2,20,int(league["participants"]));budget=e.number_input("Budget iniziale",50,5000,int(league["budget"]));matchday=f.number_input("Giornata corrente",1,38,int(league["matchday"]))
        u,v=st.columns(2)
        bench_size=u.number_input("Posti in panchina",0,15,int(league.get("bench_size",7)))
        max_substitutions=v.number_input("Sostituzioni massime",0,11,int(league.get("max_substitutions",3)))
        defense_modifier=st.checkbox("Abilita modificatore difesa",value=bool(league.get("defense_modifier_enabled",0)),
                                     help="Si applica con almeno 4 difensori alla media del portiere e dei migliori 3 difensori con voto.")
        if defense_modifier:
            st.caption("Configura tre fasce crescenti. Il bonus più alto raggiunto viene sommato al totale.")
            modifier_values={}
            modifier_columns=st.columns(3)
            for index,key in enumerate(("low","mid","high")):
                with modifier_columns[index]:
                    modifier_values[f"defense_threshold_{key}"]=st.number_input(f"Soglia {index+1}",value=float(league.get(f"defense_threshold_{key}",(6,6.5,7)[index])),step=.1)
                    modifier_values[f"defense_bonus_{key}"]=st.number_input(f"Bonus {index+1}",value=float(league.get(f"defense_bonus_{key}",(1,3,6)[index])),step=.5)
        else:
            modifier_values={key: league.get(key, default) for key,default in {
                "defense_threshold_low":6.0,"defense_threshold_mid":6.5,"defense_threshold_high":7.0,
                "defense_bonus_low":1.0,"defense_bonus_mid":3.0,"defense_bonus_high":6.0}.items()}
        st.markdown("### Fonte voti prioritaria")
        g,h=st.columns([1,2]);provider=g.text_input("Provider voti",league["vote_provider"]);source_url=h.text_input("URL feed voti",league["source_url"],placeholder=gazzetta_votes_url(league["season"]),type="password")
        p,q=st.columns(2)
        season=p.text_input("Stagione",league["season"])
        edition=q.text_input("Redazione voti esatta",league["vote_edition"],help="Gazzetta: La Gazzetta dello Sport. Fantacalcio.it: Redazione Fantacalcio, Voto Statistico oppure Voto Italia")
        st.caption("Fonte scelta per il progetto: Gazzetta / La Gazzetta dello Sport. Il collegamento legge la singola giornata della stagione indicata nell'URL.")
        st.caption("Cambiare stagione, provider o redazione separa i dati; gli archivi precedenti non vengono cancellati. Se cambi stagione, aggiorna anche l'URL Gazzetta.")
        auto=st.selectbox("Aggiornamento automatico",[0,5,15,30,60],index=[0,5,15,30,60].index(int(league["auto_sync_minutes"])) if int(league["auto_sync_minutes"]) in [0,5,15,30,60] else 0,format_func=lambda value:"Disattivato" if value==0 else f"Ogni {value} minuti")
        st.markdown("### Bonus e malus")
        keys=[("goal","Gol non su rigore"),("assist","Assist"),("yellow_card","Ammonizione"),("red_card","Espulsione"),("own_goal","Autogol"),("goal_conceded","Gol subito"),("penalty_saved","Rigore parato"),("clean_sheet","Clean sheet"),("penalty_scored","Rigore segnato"),("penalty_missed","Rigore sbagliato")]
        values={};cols=st.columns(4)
        for index,(key,label) in enumerate(keys): values[key]=cols[index%4].number_input(label,value=float(rules.get(key,{"penalty_scored":3,"penalty_missed":-3}.get(key,0.0))),step=.5,key=f"rule_{key}")
        saved=st.form_submit_button("Salva configurazione",type="primary",width="stretch")
    if saved:
        try:
            db.save_league(int(league["id"]),{"name":name,"platform":platform,"mode":mode,"participants":participants,"budget":budget,"matchday":matchday,"bench_size":bench_size,"max_substitutions":max_substitutions,"defense_modifier_enabled":defense_modifier,**modifier_values,"vote_provider":provider.strip(),"vote_edition":edition.strip(),"season":season,"source_url":source_url.strip(),"auto_sync_minutes":auto},values)
        except ValueError as exc:
            st.error(str(exc));return
        st.success("Configurazione salvata e fantavoti ricalcolati")
        st.rerun()
    st.markdown('<div class="source-note">L’app non sostituisce la redazione configurata. Il collector verifica il feed dichiarato, non effettua ricerca Web generica. Per accessi riservati consulta il README.</div>',unsafe_allow_html=True)


def sidebar(latest: dict | None) -> str:
    st.sidebar.markdown('<div class="brand"><div class="brand-mark">F</div><div class="brand-copy">FANTA<br>OPERATOR</div></div>',unsafe_allow_html=True)
    options=["▦  Panoramica","▥  Formazione","⚒  Asta live","♙  Rosa","↗  Mercato","●  Voti & dati","◆  Assistente","⚙  Impostazioni"]
    if "page" not in st.session_state or st.session_state["page"] not in options: st.session_state["page"]=options[0]
    page=st.sidebar.radio("Navigazione",options,key="page",label_visibility="collapsed")
    label,css=status_label(latest)
    st.sidebar.markdown(f'<div class="sidebar-foot"><span class="dot {"red" if css=="LIVE" else "amber" if css=="PROVVISORIO" else ""}"></span>Motore operativo attivo<br><br>Fonte: {html.escape(str(latest["source_name"])) if latest else "nessuna acquisizione"}<br>Stato: {label}</div>',unsafe_allow_html=True)
    return page


def main() -> None:
    inject_css()
    db=database();leagues=db.leagues()
    if not leagues: st.error("Nessuna lega disponibile");return
    league_id=int(st.session_state.get("league_id",leagues[0]["id"]));latest=db.latest_sync(league_id)
    league,matchday=top_controls(db,league_id,latest)
    latest=db.latest_sync(int(league["id"]),matchday)
    page=sidebar(latest)
    if not os.getenv("FANTAOPERATOR_DB"):
        st.sidebar.info("Spazio personale di questa sessione. Prima di chiudere o ricaricare la pagina, scarica il backup in Impostazioni per ritrovare rosa e formazioni.")
    st.caption("Workspace Classic personale · Voti ufficiali separati dalle stime · Operazioni sulla piattaforma della lega non inviate automaticamente")
    latest=db.latest_sync(int(league["id"]),matchday)
    auto_sync(db,league,latest)
    if "Panoramica" in page: page_overview(db,league,matchday,latest)
    elif "Formazione" in page: page_lineup(db,league,matchday)
    elif "Asta" in page: page_auction(db,league)
    elif "Rosa" in page: page_roster(db,league,matchday)
    elif "Mercato" in page: page_market(db,league)
    elif "Voti" in page: page_votes(db,league,matchday)
    elif "Assistente" in page: page_assistant(db,league,matchday)
    else: page_settings(db,league)


if __name__=="__main__": main()
