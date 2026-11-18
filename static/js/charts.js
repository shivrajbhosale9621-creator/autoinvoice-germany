/**
 * charts.js - Ink-Style Ledger Visualizations with German Locale (1.234,56 € and DD.MM.YYYY)
 */

let trendChartInstance = null;
let donutChartInstance = null;
let vendorChartInstance = null;

/**
 * Parse a German or standard decimal string strictly without silently falling back to 0.
 * Examples:
 *   "1.234,56" -> 1234.56
 *   "1.500"    -> 1500
 *   "225,80-"  -> -225.8
 *   "48.02"    -> 48.02
 *   "abc"      -> null (unparseable)
 *   ""         -> null (empty)
 */
export function parseGermanDecimal(rawVal) {
  if (rawVal === null || rawVal === undefined || typeof rawVal === "boolean") {
    return null;
  }
  if (typeof rawVal === "number") {
    return Number.isFinite(rawVal) ? rawVal : null;
  }
  let s = String(rawVal).trim();
  if (!s) return null;

  // Strip optional currency/unit tokens
  s = s.replace(/(?:EUR|USD|GBP|CHF|€|\$|£|%)/gi, "").trim();
  if (!s) return null;

  let negative = false;
  if (s.endsWith("-")) {
    negative = true;
    s = s.slice(0, -1).trim();
  } else if (s.startsWith("-")) {
    negative = true;
    s = s.slice(1).trim();
  } else if (s.startsWith("+")) {
    s = s.slice(1).trim();
  }
  if (!s) return null;

  let normalized = "";
  if (/^(?:\d{1,3}(?:\.\d{3})+|\d+),\d+$/.test(s)) {
    // German decimal with comma (e.g. "1.234,56" or "48,02")
    normalized = s.replace(/\./g, "").replace(",", ".");
  } else if (/^\d{1,3}(?:\.\d{3})+$/.test(s)) {
    // German thousands grouping integer (e.g. "1.500" -> 1500)
    normalized = s.replace(/\./g, "");
  } else if (/^\d{1,3}(?:,\d{3})+\.\d+$/.test(s)) {
    // English thousands grouping with dot decimal (e.g. "1,234.56")
    normalized = s.replace(/,/g, "");
  } else if (/^\d+(?:\.\d+)?$/.test(s)) {
    // Standard integer or dot decimal (e.g. "1234.56", "26", "0")
    normalized = s;
  } else {
    return null;
  }

  const num = Number(normalized);
  if (!Number.isFinite(num)) return null;
  return negative ? -num : num;
}

export function formatNumberDE(amount, decimals = 2) {
  const parsed = parseGermanDecimal(amount);
  const num = parsed !== null ? parsed : 0;
  return num.toLocaleString("de-DE", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

export function formatMoneyDE(amount, currency = "EUR") {
  const sym = (currency || "EUR").toUpperCase() === "EUR" ? "€" : currency;
  return `${formatNumberDE(amount, 2)} ${sym}`;
}

export function formatDateDE(dateStr) {
  if (!dateStr || dateStr === "N/A" || dateStr === "Unknown") return "-";
  const s = String(dateStr).trim();
  const isoMatch = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (isoMatch) {
    return `${isoMatch[3]}.${isoMatch[2]}.${isoMatch[1]}`;
  }
  const dmyMatch = s.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})/);
  if (dmyMatch) {
    return `${dmyMatch[1].padStart(2, "0")}.${dmyMatch[2].padStart(2, "0")}.${dmyMatch[3]}`;
  }
  return s;
}

function cleanVendorName(raw) {
  if (!raw) return "Unbekannter Lieferant";
  if (typeof raw === "object") {
    return raw.name || raw.vendor_name || raw.company_name || raw.seller || "Unbekannter Lieferant";
  }
  const str = String(raw).trim();
  if (!str || str.toLowerCase() === "unknown" || str.toLowerCase() === "unknown vendor" || str.toLowerCase() === "n/a") {
    return "Unbekannter Lieferant";
  }
  return str;
}

function parseInvoiceDate(inv) {
  const raw = inv.invoice_date || inv.date || inv.issue_date || inv.created_at || "";
  if (!raw || raw === "N/A" || raw === "Unknown") return null;
  const str = String(raw).trim();
  const isoMatch = str.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})/);
  if (isoMatch) {
    return `${isoMatch[1]}-${isoMatch[2].padStart(2, "0")}-${isoMatch[3].padStart(2, "0")}`;
  }
  const dmyMatch = str.match(/^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})/);
  if (dmyMatch) {
    return `${dmyMatch[3]}-${dmyMatch[2].padStart(2, "0")}-${dmyMatch[1].padStart(2, "0")}`;
  }
  return null;
}

export const charts = {
  initDefaults() {
    if (typeof Chart === "undefined") return;
    Chart.defaults.color = "#6E6455";
    Chart.defaults.font.family = "'IBM Plex Mono', monospace";
    Chart.defaults.font.size = 10;
    Chart.defaults.plugins.tooltip.backgroundColor = "#FDFBF5";
    Chart.defaults.plugins.tooltip.titleColor = "#1C1710";
    Chart.defaults.plugins.tooltip.bodyColor = "#1C1710";
    Chart.defaults.plugins.tooltip.borderColor = "#1C1710";
    Chart.defaults.plugins.tooltip.borderWidth = 1;
    Chart.defaults.plugins.tooltip.padding = 8;
    Chart.defaults.plugins.tooltip.cornerRadius = 0;
  },

  renderAll(invoices = []) {
    this.renderTopVendors(invoices);
    this.renderStatusDonut(invoices);
    if (typeof Chart === "undefined") return;
    this.initDefaults();
    this.renderSpendTrend(invoices);
  },

  renderSpendTrend(invoices) {
    const canvas = document.getElementById("chart-spend-trend");
    if (!canvas || typeof Chart === "undefined") return;
    const ctx = canvas.getContext("2d");

    const dateMap = {};
    invoices.forEach((inv) => {
      const parsedDate = parseInvoiceDate(inv);
      const key = parsedDate || "2026-10-06";
      const amt = parseFloat(inv.total_amount) || 0;
      dateMap[key] = (dateMap[key] || 0) + amt;
    });

    const sortedKeys = Object.keys(dateMap).sort();
    let labels = [];
    let values = [];

    if (sortedKeys.length === 0) {
      labels = ["Start", "Aktuell"];
      values = [0, 0];
    } else if (sortedKeys.length === 1) {
      const singleDate = sortedKeys[0];
      labels = ["Vorperiode", formatDateDE(singleDate)];
      values = [0, dateMap[singleDate]];
    } else {
      labels = sortedKeys.map((k) => formatDateDE(k));
      values = sortedKeys.map((k) => dateMap[k]);
    }

    if (trendChartInstance) {
      trendChartInstance.destroy();
    }

    trendChartInstance = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "Betrag (€)",
            data: values,
            borderColor: "#1C1710",
            borderWidth: 2,
            backgroundColor: "transparent",
            fill: false,
            tension: 0,
            pointBackgroundColor: "#1C1710",
            pointBorderColor: "#FDFBF5",
            pointBorderWidth: 1.5,
            pointRadius: 4,
            pointHoverRadius: 6,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (context) => formatMoneyDE(context.parsed.y),
            },
          },
        },
        scales: {
          x: {
            grid: { color: "#E2D9C4", borderDash: [2, 2] },
            ticks: { color: "#6E6455", font: { family: "'IBM Plex Mono', monospace", size: 10 } },
          },
          y: {
            grid: { color: "#E2D9C4", borderDash: [2, 2] },
            ticks: {
              color: "#6E6455",
              font: { family: "'IBM Plex Mono', monospace", size: 10 },
              callback: (val) => `${formatNumberDE(val, 0)} €`,
            },
          },
        },
      },
    });
  },

  renderStatusDonut(invoices) {
    let paidCount = 0;
    let unpaidCount = 0;
    let overdueCount = 0;

    invoices.forEach((inv) => {
      const status = (inv.payment_status || "").toLowerCase();
      if ((status.includes("paid") && !status.includes("unpaid")) || status.includes("bezahlt")) {
        paidCount++;
      } else if (status.includes("overdue") || status.includes("überfällig")) {
        overdueCount++;
      } else {
        unpaidCount++;
      }
    });

    const total = paidCount + unpaidCount + overdueCount || 1;
    const paidPct = Math.round((paidCount / total) * 100);
    const unpaidPct = Math.round((unpaidCount / total) * 100);
    const overduePct = Math.round((overdueCount / total) * 100);

    const setText = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = val;
    };

    setText("stat-paid-count", paidCount.toString());
    setText("stat-unpaid-ledger-count", unpaidCount.toString());
    setText("stat-overdue-count", overdueCount.toString());

    setText("legend-pct-paid", `${paidPct}%`);
    setText("legend-pct-unpaid", `${unpaidPct}%`);
    setText("legend-pct-overdue", `${overduePct}%`);

    const canvas = document.getElementById("chart-status-donut");
    if (!canvas || typeof Chart === "undefined") return;
    const ctx = canvas.getContext("2d");

    if (donutChartInstance) {
      donutChartInstance.destroy();
    }

    donutChartInstance = new Chart(ctx, {
      type: "doughnut",
      data: {
        labels: ["Bezahlt", "Offen", "Überfällig"],
        datasets: [
          {
            data: [paidCount, unpaidCount, overdueCount],
            backgroundColor: ["#2E7D4F", "#A86A12", "#B5342A"],
            borderColor: "#FDFBF5",
            borderWidth: 1.5,
            hoverOffset: 3,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "80%",
        plugins: { legend: { display: false } },
      },
    });
  },

  renderTopVendors(invoices) {
    const vendorMap = {};
    invoices.forEach((inv) => {
      const vendor = cleanVendorName(inv.vendor_name || inv.seller);
      if (vendor && vendor !== "Unbekannter Lieferant") {
        const amt = parseFloat(inv.total_amount) || 0;
        vendorMap[vendor] = (vendorMap[vendor] || 0) + amt;
      }
    });

    const sortedVendors = Object.entries(vendorMap)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5);

    const tableBody = document.getElementById("top-vendors-table-body");
    if (tableBody) {
      if (sortedVendors.length === 0) {
        tableBody.innerHTML = `<tr><td colspan="3" style="text-align:center; padding:1.5rem; color:var(--ink-muted);">Noch keine Lieferantenbelege erfasst.</td></tr>`;
      } else {
        tableBody.innerHTML = sortedVendors
          .map(
            ([vendor, amt], idx) => `
            <tr style="cursor:pointer;" onclick="window.switchView && window.switchView('vendors')">
              <td style="font-family:var(--font-mono); font-size:0.75rem; color:var(--ink-muted);">${idx + 1}.</td>
              <td><strong>${escapeHtml(vendor)}</strong></td>
              <td class="mono-amount" style="text-align:right;">${formatMoneyDE(amt)}</td>
            </tr>
          `
          )
          .join("");
      }
    }
  },
};

function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
