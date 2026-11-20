/**
 * api.js - Centralized HTTP Client for AutoInvoice (German Mittelstand Edition)
 * All fetch operations include credentials: "include" for httpOnly cookie authentication.
 */

const API_BASE = "";

let isRefreshing = false;
let refreshSubscribers = [];

function subscribeTokenRefresh(cb) {
  refreshSubscribers.push(cb);
}

function onRefreshed(error) {
  refreshSubscribers.forEach((cb) => cb(error));
  refreshSubscribers = [];
}

async function request(url, options = {}) {
  const mergedOptions = {
    ...options,
    credentials: "include",
  };

  let res = await fetch(url, mergedOptions);

  const isAuthEndpoint =
    url.includes("/auth/login") ||
    url.includes("/auth/signup") ||
    url.includes("/auth/refresh") ||
    url.includes("/auth/verify-otp") ||
    url.includes("/auth/resend-otp") ||
    url.includes("/auth/forgot-password") ||
    url.includes("/auth/reset-password");

  if (res.status === 401 && !isAuthEndpoint) {
    if (!isRefreshing) {
      isRefreshing = true;
      try {
        const refreshRes = await fetch(`${API_BASE}/auth/refresh`, {
          method: "POST",
          credentials: "include",
        });

        if (refreshRes.ok) {
          isRefreshing = false;
          onRefreshed(null);
          return await fetch(url, mergedOptions);
        } else {
          isRefreshing = false;
          onRefreshed(new Error("Refresh failed"));
          window.dispatchEvent(new CustomEvent("session-expired"));
          return res;
        }
      } catch (err) {
        isRefreshing = false;
        onRefreshed(err);
        window.dispatchEvent(new CustomEvent("session-expired"));
        return res;
      }
    } else {
      return new Promise((resolve) => {
        subscribeTokenRefresh((err) => {
          if (!err) {
            resolve(fetch(url, mergedOptions));
          } else {
            resolve(res);
          }
        });
      });
    }
  }

  return res;
}

export const api = {
  async getHealth() {
    try {
      const res = await request(`${API_BASE}/api/health`);
      return await res.json();
    } catch (err) {
      return { status: "error", error: err.message };
    }
  },

  // Auth
  async getMe() {
    const res = await request(`${API_BASE}/auth/me`, { method: "GET" });
    const data = await res.json();
    if (!res.ok) throw { status: res.status, data };
    return data;
  },

  async signup({ email, password }) {
    const res = await request(`${API_BASE}/auth/signup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const data = await res.json();
    if (!res.ok) throw { status: res.status, data };
    return data;
  },

  async verifyOtp({ email, code, purpose = "email_verification" }) {
    const res = await request(`${API_BASE}/auth/verify-otp`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, code, purpose }),
    });
    const data = await res.json();
    if (!res.ok) throw { status: res.status, data };
    return data;
  },

  async resendOtp({ email, purpose = "email_verification" }) {
    const res = await request(`${API_BASE}/auth/resend-otp`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, purpose }),
    });
    const data = await res.json();
    if (!res.ok) throw { status: res.status, data };
    return data;
  },

  async login({ email, password }) {
    const res = await request(`${API_BASE}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const data = await res.json();
    if (!res.ok) throw { status: res.status, data };
    return data;
  },

  async logout() {
    try {
      const res = await request(`${API_BASE}/auth/logout`, { method: "POST" });
      return await res.json();
    } catch (err) {
      console.error("Logout error:", err);
    }
  },

  async forgotPassword({ email }) {
    const res = await request(`${API_BASE}/auth/forgot-password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    const data = await res.json();
    if (!res.ok) throw { status: res.status, data };
    return data;
  },

  async resetPassword({ email, code, new_password }) {
    const res = await request(`${API_BASE}/auth/reset-password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, code, new_password }),
    });
    const data = await res.json();
    if (!res.ok) throw { status: res.status, data };
    return data;
  },

  // Invoices & FTS5 Search
  async getInvoices(includeIds = true, searchQuery = "") {
    const params = new URLSearchParams();
    params.append("include_ids", includeIds);
    if (searchQuery) params.append("q", searchQuery);
    const res = await request(`${API_BASE}/api/invoices?${params.toString()}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Belege konnten nicht geladen werden");
    return data;
  },

  async getInvoiceDetail(docId) {
    const res = await request(`${API_BASE}/api/invoice/${encodeURIComponent(docId)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Belegdetails konnten nicht geladen werden");
    return data;
  },

  async updateInvoice(docId, updates) {
    const res = await request(`${API_BASE}/api/invoice/${encodeURIComponent(docId)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Beleg konnte nicht gespeichert werden");
    return data;
  },

  async revalidateInvoice(docId) {
    const res = await request(`${API_BASE}/api/invoice/${encodeURIComponent(docId)}/validate`, {
      method: "POST",
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Validierung fehlgeschlagen");
    return data;
  },

  getXRechnungDownloadUrl(docId, leitwegId = "", useSettingsLeitweg = false) {
    const params = new URLSearchParams();
    if (leitwegId) params.append("leitweg_id", leitwegId);
    if (useSettingsLeitweg) params.append("use_settings_leitweg", "true");
    const qs = params.toString() ? `?${params.toString()}` : "";
    return `${API_BASE}/api/invoice/${encodeURIComponent(docId)}/xrechnung${qs}`;
  },
  getZugferdDownloadUrl(docId, leitwegId = "", useSettingsLeitweg = false) {
    const params = new URLSearchParams();
    if (leitwegId) params.set("leitweg_id", leitwegId);
    if (useSettingsLeitweg) params.set("use_settings_leitweg", "true");
    const qs = params.toString();
    return `${API_BASE}/api/invoice/${encodeURIComponent(docId)}/zugferd${qs ? "?" + qs : ""}`;
  },

  async deleteInvoice(docId, conversationId = "", userId = "") {
    const params = new URLSearchParams();
    if (conversationId) params.append("conversation_id", conversationId);
    if (userId) params.append("user_id", userId);
    const qs = params.toString() ? `?${params.toString()}` : "";
    const res = await request(`${API_BASE}/api/invoice/${encodeURIComponent(docId)}${qs}`, {
      method: "DELETE",
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Löschen fehlgeschlagen");
    return data;
  },

  async convertInvoiceToEur(docId, { fxRate = null, taxTreatment = null, sourceCurrency = null } = {}) {
    const body = {};
    if (fxRate !== null && String(fxRate).trim() !== "") body.fx_rate = fxRate;
    if (taxTreatment) body.tax_treatment = taxTreatment;
    if (sourceCurrency) body.source_currency = sourceCurrency;
    const res = await request(`${API_BASE}/api/invoice/${encodeURIComponent(docId)}/convert`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || data.detail || "Währungsumrechnung fehlgeschlagen");
    return data;
  },

  async uploadInvoice(file, conversationId = "", userId = "", foreign = false) {
    const formData = new FormData();
    formData.append("file", file);
    if (conversationId) formData.append("conversation_id", conversationId);
    if (userId) formData.append("user_id", userId);
    if (foreign) formData.append("foreign", "true");

    const res = await request(`${API_BASE}/api/invoice/upload`, {
      method: "POST",
      body: formData,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Upload fehlgeschlagen");
    return data;
  },

  // Settings (Task 2)
  async getSettings() {
    const res = await request(`${API_BASE}/api/settings`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Einstellungen konnten nicht geladen werden");
    return data;
  },

  async updateSettings(settingsPayload) {
    const res = await request(`${API_BASE}/api/settings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settingsPayload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Einstellungen konnten nicht gespeichert werden");
    return data;
  },

  // Vendors & Price Comparison (Task 7)
  async getVendors(itemQuery = "") {
    const qs = itemQuery ? `?item_query=${encodeURIComponent(itemQuery)}` : "";
    const res = await request(`${API_BASE}/api/vendors${qs}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Lieferanten konnten nicht geladen werden");
    return data;
  },

  async updateVendor(vendorName, payload) {
    const res = await request(`${API_BASE}/api/vendors/${encodeURIComponent(vendorName)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Lieferant konnte nicht aktualisiert werden");
    return data;
  },

  // Dashboard Summary: Cashflow, Skonto, Validation Health, Proactive Alerts (Task 8 & Tier 2)
  async getDashboardSummary() {
    const res = await request(`${API_BASE}/api/dashboard/summary`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Dashboard-Daten konnten nicht geladen werden");
    return data;
  },

  // VIES Check (Task 9)
  async checkVies(vatId, docId = "") {
    const res = await request(`${API_BASE}/api/vies/check`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vat_id: vatId, doc_id: docId }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "VIES-Prüfung fehlgeschlagen");
    return data;
  },

  // SEPA pain.001 Payment Run (Task 10)
  async previewSepaRun(invoiceIds, applySkonto = false) {
    const res = await request(`${API_BASE}/api/sepa/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ invoice_ids: invoiceIds, apply_skonto: applySkonto }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "SEPA-Vorschau fehlgeschlagen");
    return data;
  },

  async executeSepaRun({ invoiceIds, applySkonto = false, executionDate = "", humanApproved = false }) {
    const res = await request(`${API_BASE}/api/sepa/execute`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        invoice_ids: invoiceIds,
        apply_skonto: applySkonto,
        execution_date: executionDate,
        human_approved: humanApproved,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "SEPA-Zahllauf fehlgeschlagen");
    return data;
  },

  async getSepaRuns() {
    const res = await request(`${API_BASE}/api/sepa/runs`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Zahlläufe konnten nicht geladen werden");
    return data;
  },

  getSepaRunDownloadUrl(runId) {
    return `${API_BASE}/api/sepa/runs/${encodeURIComponent(runId)}/download`;
  },

  // Month-End Checklist & Tax Summary (Tier 2)
  async getMonthEnd(month = "all") {
    const res = await request(`${API_BASE}/api/month-end?month=${encodeURIComponent(month)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Monatsabschluss konnte nicht geladen werden");
    return data;
  },

  async toggleMonthEndCheck({ month = "all", itemKey, checked, markBooked = false }) {
    const res = await request(`${API_BASE}/api/month-end/check`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ month, item_key: itemKey, checked, mark_booked: markBooked }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Status konnte nicht aktualisiert werden");
    return data;
  },

  getDatevExportUrl(month = "all") {
    return `${API_BASE}/api/datev/export?month=${encodeURIComponent(month)}`;
  },

  // Chat & Copilot Action Approvals
  async chat({ message, userId, conversationId, file = null, idempotencyKey = null, lang = "de" }) {
    let body;
    let headers = {};
    const effectiveLang = lang === "en" ? "en" : "de";

    if (file) {
      body = new FormData();
      if (userId) body.append("user_id", userId);
      body.append("message", message);
      if (conversationId) body.append("conversation_id", conversationId);
      if (idempotencyKey) body.append("idempotency_key", idempotencyKey);
      body.append("lang", effectiveLang);
      body.append("file", file);
    } else {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify({
        user_id: userId,
        message: message,
        conversation_id: conversationId,
        idempotency_key: idempotencyKey,
        lang: effectiveLang,
      });
    }

    const res = await request(`${API_BASE}/api/chat`, {
      method: "POST",
      headers,
      body,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || data.detail || (effectiveLang === "en" ? "AI request failed" : "KI-Anfrage fehlgeschlagen"));
    return data;
  },

  async resolveCopilotAction(actionId, decision) {
    const res = await request(`${API_BASE}/api/chat/actions/${encodeURIComponent(actionId)}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Freigabeaktion fehlgeschlagen");
    return data;
  },

  async getConversations(userId) {
    const res = await request(`${API_BASE}/api/chat/conversations`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Konversationen konnten nicht geladen werden");
    return data;
  },

  async getChatHistory(conversationId, userId, limit = 50) {
    const res = await request(
      `${API_BASE}/api/chat/history/${encodeURIComponent(conversationId)}?limit=${limit}`
    );
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Chatverlauf konnte nicht geladen werden");
    return data;
  },

  getReportDownloadUrl(filename) {
    return `${API_BASE}/api/report/download/${encodeURIComponent(filename)}`;
  },

  async getDashboardCharts() {
    const res = await request(`${API_BASE}/api/dashboard/charts`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Diagramme konnten nicht geladen werden");
    return data;
  },

  async createDashboardChart(prompt, title = "") {
    const res = await request(`${API_BASE}/api/dashboard/charts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, title }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Diagramm konnte nicht erstellt werden");
    return data;
  },

  async deleteDashboardChart(chartId) {
    const res = await request(`${API_BASE}/api/dashboard/charts/${encodeURIComponent(chartId)}`, {
      method: "DELETE",
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Diagramm konnte nicht gelöscht werden");
    return data;
  },
};
