"""
graph_agent.py - Paper-Invoice Visualization Generator backed by SQLite (Task 1).
Generates self-contained interactive HTML charts in the Paper Invoice aesthetic
(#FDFBF5 background, #1C1710 ink, IBM Plex Mono numerals, German EUR formatting).
"""

import os
import json
import uuid
import datetime as _dt
from typing import Optional, List, Dict, Any

from config import DEFAULT_INDEX_PATH
from vector_store import (
    get_all_invoices,
    set_current_user_id,
    format_eur_de,
    format_date_de,
)

PAPER_PALETTE = ["#1C1710", "#A86A12", "#2E7D4F", "#B5342A", "#6E6455", "#8C7B5E", "#4A6B8A", "#7A3E2E"]


def _build_self_contained_chart_html(
    title: str,
    chart_type: str,
    labels: List[str],
    values: List[float],
    series_label: str = "Betrag (EUR)",
) -> str:
    """
    Build a self-contained HTML chart using Chart.js + SVG fallback in the exact
    Paper Invoice palette (#FDFBF5 card, #1C1710 ink, IBM Plex Mono numerals).
    """
    labels_json = json.dumps(labels, ensure_ascii=False)
    values_json = json.dumps([round(float(v), 2) for v in values])
    colors_json = json.dumps([PAPER_PALETTE[i % len(PAPER_PALETTE)] for i in range(max(1, len(labels)))])

    max_val = max([abs(float(v)) for v in values] + [1.0])
    bars_html = []
    for idx, (lbl, val) in enumerate(zip(labels, values)):
        pct = min(100, max(4, int((abs(float(val)) / max_val) * 100)))
        color = PAPER_PALETTE[idx % len(PAPER_PALETTE)]
        val_str = format_eur_de(val) if "EUR" in series_label or "€" in series_label else str(val)
        bars_html.append(
            f"""
            <div style="display:flex; align-items:center; gap:10px; margin-bottom:8px; font-family:'IBM Plex Mono', monospace; font-size:12px;">
              <div style="width:170px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#1C1710; font-weight:600;">{lbl}</div>
              <div style="flex:1; background:#ECE4D3; height:18px; border:1px solid #1C1710; position:relative;">
                <div style="width:{pct}%; background:{color}; height:100%;"></div>
              </div>
              <div style="width:110px; text-align:right; color:#1C1710; font-weight:700;">{val_str}</div>
            </div>
            """
        )
    fallback_rows = "\n".join(bars_html)

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <title>{title}</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    body {{
      margin: 0;
      padding: 16px;
      background: #FDFBF5;
      color: #1C1710;
      font-family: 'IBM Plex Mono', monospace;
    }}
    h3 {{
      margin: 0 0 12px 0;
      font-size: 13px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      border-bottom: 1px solid #E2D9C4;
      padding-bottom: 6px;
    }}
    #canvas-wrap {{
      width: 100%;
      height: 260px;
      position: relative;
    }}
  </style>
</head>
<body>
  <h3>{title}</h3>
  <div id="canvas-wrap">
    <canvas id="c"></canvas>
  </div>
  <div id="svg-fallback" style="display:none; padding-top:8px;">
    {fallback_rows}
  </div>
  <script>
    (function() {{
      if (typeof Chart === 'undefined') {{
        document.getElementById('canvas-wrap').style.display = 'none';
        document.getElementById('svg-fallback').style.display = 'block';
        return;
      }}
      const ctx = document.getElementById('c').getContext('2d');
      new Chart(ctx, {{
        type: {json.dumps(chart_type)},
        data: {{
          labels: {labels_json},
          datasets: [{{
            label: {json.dumps(series_label)},
            data: {values_json},
            backgroundColor: {colors_json},
            borderColor: '#1C1710',
            borderWidth: 1.5
          }}]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          plugins: {{
            legend: {{ display: {json.dumps(chart_type == 'doughnut' or chart_type == 'pie')} }}
          }}
        }}
      }});
    }})();
  </script>
</body>
</html>"""


class GraphAgent:
    """Visual Sub-Agent backed directly by SQLite."""

    def __init__(self, llm=None, index_path: str = DEFAULT_INDEX_PATH):
        self.llm = llm
        self.index_path = index_path
        self.user_id: Optional[str] = None

    def run(self, request: str) -> dict:
        set_current_user_id(self.user_id)
        invoices = get_all_invoices(user_id=self.user_id)
        if not invoices:
            return {
                "status": "failed",
                "chart_type": "none",
                "figures": [],
                "chart_paths": [],
                "message": "Keine Rechnungen in der Datenbank gefunden. / No invoices found in storage.",
                "error": "No invoices in storage",
            }

        req_low = (request or "").lower()
        chart_type = "bar"
        title = "Ausgaben nach Lieferant (EUR)"
        labels: List[str] = []
        values: List[float] = []
        series_label = "Brutto (EUR)"

        if any(w in req_low for w in ["status", "paid", "unpaid", "bezahlt", "offen", "pie", "donut"]):
            chart_type = "doughnut"
            title = "Zahlungsstatus der Belege"
            series_label = "Anzahl Belege"
            counts: Dict[str, int] = {}
            for inv in invoices:
                st = str(inv.get("payment_status") or "Unpaid")
                counts[st] = counts.get(st, 0) + 1
            labels = list(counts.keys())
            values = [float(v) for v in counts.values()]

        elif any(w in req_low for w in ["item", "position", "artikel", "product", "line"]):
            chart_type = "bar"
            title = "Top-Positionen nach Netto-Volumen (EUR)"
            item_sums: Dict[str, float] = {}
            for inv in invoices:
                for it in inv.get("line_items") or []:
                    desc = str(it.get("description") or it.get("article_number") or "Position")[:28]
                    item_sums[desc] = item_sums.get(desc, 0.0) + float(it.get("line_total") or 0.0)
            top_items = sorted(item_sums.items(), key=lambda x: abs(x[1]), reverse=True)[:6]
            labels = [k for k, _ in top_items]
            values = [round(v, 2) for _, v in top_items]

        elif any(w in req_low for w in ["time", "date", "trend", "zeit", "verlauf", "datum", "monat"]):
            chart_type = "line"
            title = "Rechnungsbeträge im Zeitverlauf (EUR)"
            by_date: Dict[str, float] = {}
            for inv in invoices:
                d = str(inv.get("invoice_date") or "N/A")
                by_date[d] = by_date.get(d, 0.0) + float(inv.get("total_amount") or 0.0)
            for d_key in sorted(by_date.keys()):
                labels.append(format_date_de(d_key) or d_key)
                values.append(round(by_date[d_key], 2))

        else:
            chart_type = "bar"
            title = "Umsatzvolumen nach Lieferant (EUR)"
            by_vendor: Dict[str, float] = {}
            for inv in invoices:
                v = str(inv.get("vendor_name") or "Unbekannt")
                by_vendor[v] = by_vendor.get(v, 0.0) + float(inv.get("total_amount") or 0.0)
            top_v = sorted(by_vendor.items(), key=lambda x: abs(x[1]), reverse=True)[:6]
            labels = [k for k, _ in top_v]
            values = [round(v, 2) for _, v in top_v]

        if not labels:
            return {
                "status": "failed",
                "chart_type": "none",
                "figures": [],
                "chart_paths": [],
                "message": "Keine Daten für das Diagramm verfügbar.",
                "error": "No data points",
            }

        static_charts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "charts")
        os.makedirs(static_charts_dir, exist_ok=True)
        chart_id = f"chart_{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:8]}_0.html"
        chart_file_path = os.path.join(static_charts_dir, chart_id)

        html = _build_self_contained_chart_html(title, chart_type, labels, values, series_label)
        with open(chart_file_path, "w", encoding="utf-8") as f:
            f.write(html)

        rel_path = f"/static/charts/{chart_id}"
        return {
            "status": "success",
            "chart_type": chart_type,
            "figures": [rel_path],
            "chart_paths": [rel_path],
            "message": f"Diagramm '{title}' wurde aus SQLite-Daten erstellt.",
            "error": None,
        }
