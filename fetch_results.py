#!/usr/bin/env python3
"""Fetch FDJ draw results and generate a static GitHub Pages site."""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import re
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin


ROOT = Path(__file__).resolve().parent

FDJ_URLS = {
    "loto": "https://www.fdj.fr/jeux-de-tirage/loto/resultats",
    "euromillions": "https://www.fdj.fr/jeux-de-tirage/euromillions-my-million/resultats",
}

FDJ_HISTORY_URLS = {
    "loto": "https://www.fdj.fr/jeux-de-tirage/loto/historique",
    "euromillions": "https://www.fdj.fr/jeux-de-tirage/euromillions-my-million/historique",
}

FDJ_LATEST_ARCHIVES = {
    "loto": "loto_201911",
    "euromillions": "euromillions_202002",
}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
)

DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MONTHS = [
    "janvier",
    "fevrier",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "aout",
    "septembre",
    "octobre",
    "novembre",
    "decembre",
]


class FDJError(RuntimeError):
    """Raised when FDJ data cannot be fetched or parsed."""


def fetch_html(url: str) -> tuple[str, str]:
    """Return page HTML and the effective FDJ URL.

    FDJ currently returns a 308 redirect response that already contains the
    rendered result page body. urllib raises on that status, so we accept it.
    """

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.6",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            effective_url = response.geturl()
    except urllib.error.HTTPError as exc:
        if exc.code != 308:
            raise
        body = exc.read()
        effective_url = urljoin(url, exc.headers.get("Location") or url)

    return body.decode("utf-8", "replace"), effective_url


def fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.6",
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def decode_next_flight(html_text: str) -> str:
    chunks: list[str] = []
    pattern = r'self\.__next_f\.push\(\[1,("(?:\\.|[^"\\])*")\]\)</script>'

    for match in re.finditer(pattern, html_text):
        try:
            chunks.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            continue

    if not chunks:
        raise FDJError("Impossible de trouver les donnees Next.js dans la page FDJ.")

    return "".join(chunks)


def extract_balanced(text: str, start: int) -> str:
    opening = text[start]
    pairs = {"{": "}", "[": "]"}
    if opening not in pairs:
        raise FDJError(f"Caractere inattendu a la position {start}: {opening!r}")

    closing = pairs[opening]
    depth = 0
    in_string = False
    escaped = False

    for index, char in enumerate(text[start:], start):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    raise FDJError("Bloc JSON incomplet dans la page FDJ.")


def query_arrays_from_stream(stream: str) -> list[list[dict[str, Any]]]:
    arrays: list[list[dict[str, Any]]] = []

    for match in re.finditer(r'"queries":\[', stream):
        raw_array = extract_balanced(stream, match.end() - 1)
        try:
            parsed = json.loads(raw_array)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            arrays.append(parsed)

    return arrays


def query_game_name(query_key: list[Any]) -> str | None:
    if len(query_key) < 2 or not isinstance(query_key[1], str):
        return None
    try:
        params = json.loads(query_key[1])
    except json.JSONDecodeError:
        return None
    game_name = params.get("gameName")
    return game_name if isinstance(game_name, str) else None


def collect_fdj_data() -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    results: dict[str, Any] = {}
    announces: dict[str, Any] = {}
    source_urls: list[str] = []

    for url in FDJ_URLS.values():
        html_text, effective_url = fetch_html(url)
        source_urls.append(effective_url)
        stream = decode_next_flight(html_text)

        for query_array in query_arrays_from_stream(stream):
            for query in query_array:
                query_key = query.get("queryKey")
                if not isinstance(query_key, list) or not query_key:
                    continue

                key_name = query_key[0]
                game_name = query_game_name(query_key)
                data = query.get("state", {}).get("data")
                if not game_name or not data:
                    continue

                if key_name == "drawGameResultByDate":
                    results[game_name] = data[0] if isinstance(data, list) else data
                elif key_name == "drawGameAnnounce":
                    announces[game_name] = data

    missing = [game for game in ("loto", "euromillions") if game not in results]
    if missing:
        raise FDJError("Resultats manquants: " + ", ".join(missing))

    return results, announces, sorted(set(source_urls))


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def format_date(value: str) -> str:
    date = parse_iso(value)
    return f"{DAYS[date.weekday()]} {date.day} {MONTHS[date.month - 1]} {date.year}"


def format_time(value: str) -> str:
    date = parse_iso(value)
    return f"{date.hour:02d}h{date.minute:02d}"


def sorted_numbers(values: list[Any]) -> list[int]:
    return sorted(int(value) for value in values)


def pick_announce(game: str, announce_data: Any) -> dict[str, Any]:
    if isinstance(announce_data, dict):
        return announce_data
    if not isinstance(announce_data, list) or not announce_data:
        return {}

    if game == "euromillions":
        for item in announce_data:
            if item.get("gameExternalId") == "draw-20":
                return item
    if game == "loto":
        for item in announce_data:
            if item.get("gameExternalId") == "draw-13":
                return item

    return announce_data[0]


def next_draw_summary(announce: dict[str, Any]) -> str:
    if not announce:
        return ""

    bits = [announce.get("formattedDate", ""), announce.get("amount", "")]
    extra = " ".join(
        str(announce.get(key) or "").strip()
        for key in ("suffix1", "suffix2")
        if announce.get(key)
    )
    if extra:
        bits.append(extra)

    return " - ".join(bit for bit in bits if bit)


def parse_csv_date(value: str) -> date:
    return datetime.strptime(value, "%d/%m/%Y").date()


def format_plain_date(value: date) -> str:
    return f"{DAYS[value.weekday()]} {value.day} {MONTHS[value.month - 1]} {value.year}"


def archive_download_url(game: str) -> str:
    html_text, _ = fetch_html(FDJ_HISTORY_URLS[game])
    wanted = FDJ_LATEST_ARCHIVES[game]

    for anchor in re.finditer(r"<a\b[^>]*>", html_text):
        attrs = dict(re.findall(r'([:\w-]+)="([^"]*)"', anchor.group(0)))
        if attrs.get("download") == wanted and attrs.get("href"):
            return html.unescape(attrs["href"])

    raise FDJError(f"Archive FDJ introuvable pour {game}.")


def archive_rows(game: str) -> list[dict[str, str]]:
    archive = fetch_bytes(archive_download_url(game))

    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        csv_names = [name for name in zipped.namelist() if name.lower().endswith(".csv")]
        if not csv_names:
            raise FDJError(f"Aucun CSV dans l'archive {game}.")
        csv_text = zipped.read(csv_names[0]).decode("utf-8-sig")

    return list(csv.DictReader(io.StringIO(csv_text), delimiter=";"))


def int_values(row: dict[str, str], keys: list[str]) -> list[int]:
    return sorted(int(row[key]) for key in keys if row.get(key))


def normalize_loto_archive_row(row: dict[str, str]) -> dict[str, Any]:
    draw_date = parse_csv_date(row["date_de_tirage"])
    return {
        "external_id": str(row.get("annee_numero_de_tirage", "")),
        "date_key": draw_date.isoformat(),
        "draw_date": format_plain_date(draw_date),
        "draw_time": "20h55",
        "numbers": int_values(row, ["boule_1", "boule_2", "boule_3", "boule_4", "boule_5"]),
        "chance": int_values(row, ["numero_chance"]),
        "second_draw": int_values(
            row,
            [
                "boule_1_second_tirage",
                "boule_2_second_tirage",
                "boule_3_second_tirage",
                "boule_4_second_tirage",
                "boule_5_second_tirage",
            ],
        ),
        "joker": row.get("numero_7", ""),
        "raffle_codes": [
            code.strip()
            for code in row.get("codes_gagnants", "").split(",")
            if code.strip()
        ],
    }


def normalize_euro_archive_row(row: dict[str, str]) -> dict[str, Any]:
    draw_date = parse_csv_date(row["date_de_tirage"])
    my_million = row.get("numero_My_Million", "").strip()
    return {
        "external_id": str(row.get("annee_numero_de_tirage", "")),
        "date_key": draw_date.isoformat(),
        "draw_date": format_plain_date(draw_date),
        "draw_time": "21h45",
        "numbers": int_values(row, ["boule_1", "boule_2", "boule_3", "boule_4", "boule_5"]),
        "stars": int_values(row, ["etoile_1", "etoile_2"]),
        "my_million": [my_million] if my_million else [],
    }


def build_archive_history(game: str, days: int = 31) -> list[dict[str, Any]]:
    cutoff = datetime.now().date() - timedelta(days=days)
    normalizer = normalize_loto_archive_row if game == "loto" else normalize_euro_archive_row
    history: list[dict[str, Any]] = []

    for row in archive_rows(game):
        draw_date = parse_csv_date(row["date_de_tirage"])
        if draw_date < cutoff:
            break
        history.append(normalizer(row))

    return history


def normalize_loto_live_result(result: dict[str, Any], next_draw: str) -> dict[str, Any]:
    draw_date = parse_iso(result["date"]).date()
    item = {
        "name": "LOTO",
        "external_id": str(result.get("externalId", "")),
        "date_key": draw_date.isoformat(),
        "draw_date": format_date(result["date"]),
        "draw_time": format_time(result["date"]),
        "numbers": sorted_numbers(result.get("numbers", [])),
        "chance": sorted_numbers(result.get("complementariesNumbers", [])),
        "second_draw": sorted_numbers(result.get("options", {}).get("numbers", [])),
        "joker": result.get("options", {}).get("joker", ""),
        "raffle_codes": result.get("raffleCodes", []),
        "next_draw": next_draw,
    }
    return item


def normalize_euro_live_result(result: dict[str, Any], next_draw: str) -> dict[str, Any]:
    draw_date = parse_iso(result["date"]).date()
    item = {
        "name": "EuroMillions",
        "external_id": str(result.get("externalId", "")),
        "date_key": draw_date.isoformat(),
        "draw_date": format_date(result["date"]),
        "draw_time": format_time(result["date"]),
        "numbers": sorted_numbers(result.get("numbers", [])),
        "stars": sorted_numbers(result.get("complementariesNumbers", [])),
        "my_million": result.get("myMillion", []),
        "next_draw": next_draw,
    }
    return item


def merge_current_history(current: dict[str, Any], history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = [current]
    current_key = current.get("date_key")
    for item in history:
        if item.get("date_key") != current_key:
            merged.append(item)
    return merged


def build_bundle() -> dict[str, Any]:
    raw_results, raw_announces, source_urls = collect_fdj_data()
    loto_result = raw_results["loto"]
    euro_result = raw_results["euromillions"]
    loto_announce = pick_announce("loto", raw_announces.get("loto"))
    euro_announce = pick_announce("euromillions", raw_announces.get("euromillions"))
    loto = normalize_loto_live_result(loto_result, next_draw_summary(loto_announce))
    euro = normalize_euro_live_result(euro_result, next_draw_summary(euro_announce))
    loto_history = merge_current_history(loto, build_archive_history("loto"))
    euro_history = merge_current_history(euro, build_archive_history("euromillions"))

    now = datetime.now().astimezone()

    bundle = {
        "updated_at": now.isoformat(timespec="seconds"),
        "updated_label": f"{format_date(now.isoformat())} a {format_time(now.isoformat())}",
        "source_urls": sorted(set(source_urls + list(FDJ_HISTORY_URLS.values()))),
        "loto": {**loto, "history": loto_history},
        "euromillions": {**euro, "history": euro_history},
    }

    return bundle


def escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def balls(values: list[int], kind: str = "main") -> str:
    return "".join(f'<span class="ball {kind}">{escape(value)}</span>' for value in values)


def render_html(bundle: dict[str, Any]) -> str:
    loto = bundle["loto"]
    euro = bundle["euromillions"]
    history_json = json.dumps(
        {
            "loto": loto["history"],
            "euromillions": euro["history"],
        },
        ensure_ascii=False,
    ).replace("</", "<\\/")
    sources = "\n".join(
        f'<a href="{escape(url)}" rel="noreferrer">{escape(url)}</a>' for url in bundle["source_urls"]
    )

    return f"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#fff6df">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-title" content="Resultats FDJ">
  <meta name="apple-mobile-web-app-status-bar-style" content="default">
  <title>Resultats Loto et EuroMillions</title>
  <link rel="manifest" href="./manifest.webmanifest">
  <style>
    :root {{
      --ink: #1b1d2a;
      --muted: #5f6472;
      --paper: #fffdf8;
      --panel: #ffffff;
      --line: #dedfd7;
      --blue: #214ed3;
      --red: #c7353f;
      --green: #23765a;
      --gold: #f0c443;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background:
        linear-gradient(180deg, rgba(240,196,67,.18), rgba(35,118,90,.10) 42%, rgba(33,78,211,.10)),
        var(--paper);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{
      width: min(1100px, calc(100% - 28px));
      margin: 0 auto;
      padding: 28px 0 34px;
    }}
    header {{
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 18px;
      margin-bottom: 20px;
    }}
    h1 {{
      margin: 0;
      font-size: clamp(2rem, 5vw, 4.6rem);
      line-height: 1;
      letter-spacing: 0;
    }}
    .updated {{
      margin: 10px 0 0;
      color: var(--muted);
      font-size: 1rem;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
    }}
    .game-card {{
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255,255,255,.92);
      box-shadow: 0 14px 34px rgba(23, 31, 47, .09);
      padding: clamp(18px, 3vw, 28px);
    }}
    .game-card h2 {{
      margin: 0;
      font-size: clamp(1.6rem, 4vw, 3rem);
      line-height: 1;
      letter-spacing: 0;
    }}
    .history-picker {{
      display: grid;
      gap: 6px;
      margin: 18px 0 12px;
      color: var(--muted);
      font-size: .95rem;
      font-weight: 800;
      text-transform: uppercase;
    }}
    .history-picker select {{
      width: 100%;
      min-height: 48px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      color: var(--ink);
      font: inherit;
      font-size: 1.05rem;
      font-weight: 700;
      text-transform: none;
      padding: 8px 12px;
    }}
    .date {{
      margin: 8px 0 22px;
      color: var(--muted);
      font-size: 1.08rem;
    }}
    .balls {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin: 0 0 18px;
    }}
    .ball {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: clamp(48px, 10vw, 72px);
      aspect-ratio: 1;
      border-radius: 50%;
      color: #fff;
      font-weight: 800;
      font-size: clamp(1.35rem, 3vw, 2.1rem);
      line-height: 1;
      box-shadow: inset 0 -6px 0 rgba(0,0,0,.16);
    }}
    .main {{ background: var(--blue); }}
    .chance {{ background: var(--red); }}
    .star {{ background: var(--gold); color: #332b00; }}
    .tag {{
      display: inline-flex;
      align-items: center;
      min-height: 42px;
      border-radius: 8px;
      border: 1px solid var(--line);
      background: #f7f7f1;
      padding: 8px 12px;
      font-weight: 700;
      color: var(--green);
      overflow-wrap: anywhere;
    }}
    .section-title {{
      margin: 20px 0 8px;
      color: var(--muted);
      font-size: .92rem;
      font-weight: 800;
      text-transform: uppercase;
    }}
    .detail {{
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 1rem;
    }}
    footer {{
      margin-top: 18px;
      color: var(--muted);
      font-size: .9rem;
    }}
    footer a {{
      color: inherit;
      overflow-wrap: anywhere;
    }}
    @media (max-width: 760px) {{
      header {{
        display: block;
      }}
      .grid {{
        grid-template-columns: 1fr;
      }}
      .game-card {{
        padding: 18px;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Resultats FDJ</h1>
        <p class="updated">Mis a jour le {escape(bundle["updated_label"])}</p>
      </div>
    </header>

    <div class="grid">
      <section class="game-card" aria-labelledby="loto-title">
        <h2 id="loto-title">LOTO</h2>
        <label class="history-picker" for="loto-history">
          <span>Date du tirage</span>
          <select id="loto-history"></select>
        </label>
        <p class="date" id="loto-date">{escape(loto["draw_date"])} a {escape(loto["draw_time"])}</p>
        <div class="balls" id="loto-numbers" aria-label="Numeros LOTO">{balls(loto["numbers"])}</div>
        <div class="section-title">Numero Chance</div>
        <div class="balls" id="loto-chance" aria-label="Numero Chance">{balls(loto["chance"], "chance")}</div>
        <div class="section-title">Second tirage</div>
        <div class="balls" id="loto-second" aria-label="Second tirage LOTO">{balls(loto["second_draw"])}</div>
        <p class="detail" id="loto-joker">Joker+ : <strong>{escape(loto["joker"] or "non publie")}</strong></p>
        <p class="detail">Prochain tirage : <strong>{escape(loto["next_draw"] or "non publie")}</strong></p>
      </section>

      <section class="game-card" aria-labelledby="euro-title">
        <h2 id="euro-title">EuroMillions</h2>
        <label class="history-picker" for="euro-history">
          <span>Date du tirage</span>
          <select id="euro-history"></select>
        </label>
        <p class="date" id="euro-date">{escape(euro["draw_date"])} a {escape(euro["draw_time"])}</p>
        <div class="balls" id="euro-numbers" aria-label="Numeros EuroMillions">{balls(euro["numbers"])}</div>
        <div class="section-title">Etoiles</div>
        <div class="balls" id="euro-stars" aria-label="Etoiles EuroMillions">{balls(euro["stars"], "star")}</div>
        <div class="section-title">My Million</div>
        <div class="tag" id="euro-my-million">{escape(", ".join(euro["my_million"]) or "non publie")}</div>
        <p class="detail">Prochain tirage : <strong>{escape(euro["next_draw"] or "non publie")}</strong></p>
      </section>
    </div>

    <footer>
      <p>Resultats communiques a titre indicatif. Pour un gain, verifier le recu et les resultats officiels FDJ.</p>
      <p>Sources FDJ : {sources}</p>
    </footer>
  </main>
  <script id="results-data" type="application/json">{history_json}</script>
  <script>
    const resultsData = JSON.parse(document.getElementById("results-data").textContent);

    function escapeHtml(value) {{
      return String(value ?? "").replace(/[&<>"']/g, (char) => ({{
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      }}[char]));
    }}

    function renderBalls(values, kind) {{
      return (values || [])
        .map((value) => `<span class="ball ${{kind}}">${{escapeHtml(value)}}</span>`)
        .join("");
    }}

    function fillSelect(selectId, items) {{
      const select = document.getElementById(selectId);
      select.innerHTML = items
        .map((item, index) => `<option value="${{index}}">${{escapeHtml(item.draw_date)}}</option>`)
        .join("");
      return select;
    }}

    function renderLoto(item) {{
      document.getElementById("loto-date").textContent = `${{item.draw_date}} a ${{item.draw_time}}`;
      document.getElementById("loto-numbers").innerHTML = renderBalls(item.numbers, "main");
      document.getElementById("loto-chance").innerHTML = renderBalls(item.chance, "chance");
      document.getElementById("loto-second").innerHTML = renderBalls(item.second_draw, "main");
      document.getElementById("loto-joker").innerHTML =
        `Joker+ : <strong>${{escapeHtml(item.joker || "non publie")}}</strong>`;
    }}

    function renderEuroMillions(item) {{
      document.getElementById("euro-date").textContent = `${{item.draw_date}} a ${{item.draw_time}}`;
      document.getElementById("euro-numbers").innerHTML = renderBalls(item.numbers, "main");
      document.getElementById("euro-stars").innerHTML = renderBalls(item.stars, "star");
      document.getElementById("euro-my-million").textContent =
        (item.my_million || []).join(", ") || "non publie";
    }}

    function setupHistory(game, selectId, render) {{
      const items = resultsData[game] || [];
      const select = fillSelect(selectId, items);
      select.addEventListener("change", () => render(items[Number(select.value)]));
      if (items.length > 0) {{
        render(items[0]);
      }}
    }}

    setupHistory("loto", "loto-history", renderLoto);
    setupHistory("euromillions", "euro-history", renderEuroMillions);
  </script>
</body>
</html>
"""


def render_manifest() -> str:
    manifest = {
        "name": "Resultats FDJ",
        "short_name": "Resultats FDJ",
        "start_url": "./",
        "scope": "./",
        "display": "standalone",
        "background_color": "#fffdf8",
        "theme_color": "#fff6df",
    }
    return json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"


def write_outputs(bundle: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "index.html").write_text(render_html(bundle), encoding="utf-8")
    (output_dir / "manifest.webmanifest").write_text(render_manifest(), encoding="utf-8")
    (output_dir / ".nojekyll").write_text("", encoding="utf-8")
    (output_dir / "resultats.json").write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recupere les resultats FDJ.")
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "dist"),
        help="Dossier de sortie pour index.html, resultats.json et manifest.webmanifest.",
    )
    parser.add_argument("--print-json", action="store_true", help="Affiche aussi le JSON.")
    args = parser.parse_args(argv)

    try:
        bundle = build_bundle()
        output_dir = Path(args.output_dir).expanduser().resolve()
        write_outputs(bundle, output_dir)
    except Exception as exc:  # noqa: BLE001 - CLI should print a concise failure.
        print(f"Erreur: {exc}", file=sys.stderr)
        return 1

    print(f"Page generee: {output_dir / 'index.html'}")

    if args.print_json:
        print(json.dumps(bundle, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
