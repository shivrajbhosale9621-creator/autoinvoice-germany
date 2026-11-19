/**
 * app.js - Main Application Orchestrator for AutoInvoice (German Mittelstand Edition)
 * Implements:
 * - Task 2: Settings Page (Company profile, DATEV SKR03/SKR04, Locale EUR / 1.234,56 / DD.MM.YYYY)
 * - Task 3: Collapsible Sidebar with localStorage persistence + Mobile Slide-in Drawer
 * - Task 4: Per-Invoice Detail Page with editable fields saved back to SQLite
 * - Task 5: 9-Rule Validation Report (V1-V9) + Line-level validation flags
 * - Task 6: XRechnung EN 16931 XML download
 * - Task 7: Vendor Pages + Side-by-side Item Price Comparison (cheapest flagged)
 * - Task 8: Cash Flow & Skonto Panel on Dashboard
 * - Task 9: EU VIES VAT Check badge & on-demand recheck
 * - Task 10: SEPA pain.001.001.03 Payment Run with IBAN Checksum, Human Approval & Audit Trail
 * - Task 11: Loud IBAN Change Fraud Alert on Detail Page, Dashboard & Vendors Page
 * - Task 12: German UI Labels (default DE) with DE/EN toggle + DD.MM.YYYY & 1.234,56 everywhere
 * - Tier 2: Month-End Checklist, Tax Summary (19/7/0%), Validation Health, Vendor Scores,
 *           Duplicate Detection, Proactive Alerts, Chat Citations, and Copilot Action Approvals.
 */

import { api } from "./api.js";
import { charts, formatMoneyDE, formatNumberDE, formatDateDE, parseGermanDecimal } from "./charts.js";
import { initUpload } from "./upload.js";
import { initChat } from "./chat.js";

const I18N = {
  de: {
    brand_sub: "DATEV • XRECHNUNG",
    nav_dashboard: "Übersicht",
    nav_upload: "Rechnungseingang",
    nav_ledger: "Belege",
    nav_vendors: "Lieferanten",
    nav_payments: "Zahllauf (SEPA)",
    nav_monthend: "Monatsabschluss",
    nav_chat: "KI-Assistent",
    nav_settings: "Einstellungen",
    btn_receive_invoice: "BELEG ERFASSEN",
    btn_logout: "ABMELDEN",
    dash_proactive_alerts: "PROAKTIVE HINWEISE, FRISTEN & BETRUGSWARNUNGEN",
    dash_total_volume: "GESAMTES RECHNUNGSVOLUMEN (BRUTTO)",
    dash_audited_live: "GEPRÜFT • SQLITE",
    dash_avg_voucher: "Ø BELEGWERT:",
    dash_currency: "WÄHRUNG:",
    dash_recorded_entries: "ERFASSTE BELEGE",
    dash_validation_health: "PRÜFQUOTE (V1-V8)",
    dash_cashflow_skonto: "LIQUIDITÄTSVORSCHAU & SKONTO-POTENZIAL",
    btn_open_sepa: "Zum SEPA-Zahllauf →",
    cf_due_week: "FÄLLIG DIESE WOCHE",
    cf_due_month: "FÄLLIG DIESEN MONAT",
    cf_skonto_avail: "SKONTO-ERSPARNIS VERFÜGBAR",
    cf_skonto_table_title: "SKONTO-FRISTEN & ERSPARNIS PRO RECHNUNG",
    dash_val_report_title: "VALIDIERUNGSSTATUS & HÄUFIGSTE FEHLERGRÜNDE",
    dash_spend_trajectory: "AUSGABENVERLAUF & ZAHLUNGSSTROM",
    dash_settlement_audit: "ZAHLUNGSSTATUS",
    status_paid: "BEZAHLT",
    status_open: "OFFEN",
    status_overdue: "ÜBERFÄLLIG",
    dash_top_vendors: "TOP-LIEFERANTEN NACH UMSATZ",
    dash_quick_actions: "SCHNELLAKTIONEN & KI-PRÜFUNG",
    dash_recent_invoices: "AKTUELLE BELEGE (KLICK AUF BELEG-NR. ÖFFNET DETAILSEITE)",
    btn_all_invoices: "Alle Belege anzeigen →",
    upload_title: "Rechnungsbeleg zur Prüfung ablegen",
    upload_btn_select: "BELEG AUSWÄHLEN",
    filter_all: "ALLE BELEGE",
    filter_open: "OFFEN",
    filter_paid: "BEZAHLT",
    filter_failed: "PRÜFFEHLER",
    th_inv_no: "RECHNUNGS-NR.",
    th_vendor: "LIEFERANT",
    th_vat_id: "UST-IDNR.",
    th_date: "BELEGDATUM",
    th_due: "FÄLLIGKEIT",
    th_net: "NETTO",
    th_vat: "UST",
    th_gross: "BRUTTO",
    th_amount_eur: "BETRAG (EUR)",
    th_validation: "PRÜFUNG",
    th_status: "STATUS",
    th_action: "AKTION",
    vendors_title: "LIEFERANTENVERZEICHNIS (KLICK ZEIGT ALLE BELEGE DES LIEFERANTEN)",
    vendors_price_comparison: "ARTIKEL-PREISVERGLEICH (EINZELPREISE NEBENEINANDER, GÜNSTIGSTER MARKIERT)",
    sepa_title: "SEPA-ZAHLLAUF (PAIN.001.001.03 DEUTSCHER DK-STANDARD)",
    sepa_audit_trail: "ZAHLLAUF-PRÜFPFAD (AUDIT TRAIL: WELCHE DATEI WELCHE RECHNUNGEN BEZAHLT HAT)",
    monthend_tax_title: "STEUERÜBERSICHT & UMSATZSTEUER NACH STEUERSATZ (19%, 7%, 0%) FÜR DEN STEUERBERATER",
    monthend_checklist_title: "MONATSABSCHLUSS-CHECKLISTE FÜR DIE BUCHHALTUNG",
    settings_title: "UNTERNEHMENSPROFIL, DATEV-KONTENRAHMEN & GEBIETSSCHEMA (LOCALE)",
    btn_save_settings: "EINSTELLUNGEN SPEICHERN",
    settings_company_hdr: "Firmendaten",
    lbl_company_name: "Firmenname",
    lbl_company_address: "Firmenadresse",
    lbl_company_vat: "USt-IdNr.",
    settings_datev_hdr: "DATEV-Export",
    settings_locale_hdr: "Sprache und Formate",
    th_skonto_deadline: "Skontofrist",
    th_skonto_saving: "Skontoersparnis",
    th_net_payable: "Nettobetrag",
    th_payment_terms: "Zahlungsbedingungen",
    th_total_gross: "Gesamtbetrag brutto",
    chat_new_session: "Neue Unterhaltung",
    upload_foreign_checkbox: "Auslandsrechnung / Foreign invoice",
    upload_foreign_hint: "Fremdwährung & KI-Extraktion (Groq)",
    fx_section_title: "Fremdwährung, EZB-Umrechnung & Steuerbehandlung",
    lbl_supplier_country: "Lieferantenland (ISO)",
    lbl_source_currency: "Ausgangswährung",
    lbl_fx_rate: "Wechselkurs (Fremdwährung je 1 EUR)",
    lbl_fx_date_source: "Kursdatum & Quelle",
    lbl_tax_treatment: "Steuerbehandlung",
    tt_standard: "Regelbesteuerung (Standard)",
    tt_reverse_charge: "Reverse Charge (§ 13b UStG)",
    tt_import_no_vat: "Import / Drittland (ohne USt)",
    lbl_original_amounts: "Originalbeträge (Ausgangswährung)",
    lbl_eur_amounts: "Umgerechnete EUR-Beträge",
    btn_convert_eur: "In EUR umrechnen",
    lbl_suggested_prefix: "Vorschlag:",
  },
  en: {
    brand_sub: "DATEV • XRECHNUNG",
    nav_dashboard: "Dashboard",
    nav_upload: "Incoming Tray",
    nav_ledger: "Invoices Ledger",
    nav_vendors: "Vendors",
    nav_payments: "Payment Run (SEPA)",
    nav_monthend: "Month-End & Tax",
    nav_chat: "AI Copilot",
    nav_settings: "Settings",
    btn_receive_invoice: "UPLOAD INVOICE",
    btn_logout: "LOG OUT",
    dash_proactive_alerts: "PROACTIVE ALERTS, DEADLINES & FRAUD SIGNALS",
    dash_total_volume: "TOTAL INVOICED VOLUME (GROSS)",
    dash_audited_live: "AUDITED • SQLITE",
    dash_avg_voucher: "AVG INVOICE:",
    dash_currency: "CURRENCY:",
    dash_recorded_entries: "RECORDED INVOICES",
    dash_validation_health: "VALIDATION PASS RATE",
    dash_cashflow_skonto: "CASH FLOW & SKONTO SAVINGS PANEL",
    btn_open_sepa: "Open SEPA Payment Run →",
    cf_due_week: "DUE THIS WEEK",
    cf_due_month: "DUE THIS MONTH",
    cf_skonto_avail: "SKONTO SAVINGS AVAILABLE",
    cf_skonto_table_title: "SKONTO DEADLINES & SAVING PER INVOICE",
    dash_val_report_title: "VALIDATION HEALTH & TOP FAILURE REASONS",
    dash_spend_trajectory: "SPEND TRAJECTORY & CASH FLOW",
    dash_settlement_audit: "PAYMENT STATUS",
    status_paid: "PAID",
    status_open: "OPEN",
    status_overdue: "OVERDUE",
    dash_top_vendors: "TOP VENDORS BY SPEND",
    dash_quick_actions: "QUICK DISPATCHES & AI AUDIT",
    dash_recent_invoices: "RECENT INVOICES (CLICK INVOICE # FOR DETAIL PAGE)",
    btn_all_invoices: "View All Invoices →",
    upload_title: "Deposit invoice document for validation",
    upload_btn_select: "SELECT DOCUMENT",
    filter_all: "ALL INVOICES",
    filter_open: "OPEN",
    filter_paid: "PAID",
    filter_failed: "FAILED CHECKS",
    th_inv_no: "INVOICE #",
    th_vendor: "VENDOR",
    th_vat_id: "VAT ID",
    th_date: "DOC DATE",
    th_due: "DUE DATE",
    th_net: "NET",
    th_vat: "VAT",
    th_gross: "GROSS",
    th_amount_eur: "AMOUNT (EUR)",
    th_validation: "VALIDATION",
    th_status: "STATUS",
    th_action: "ACTION",
    vendors_title: "VENDORS DIRECTORY (CLICK A VENDOR TO VIEW ALL THEIR INVOICES)",
    vendors_price_comparison: "ITEM PRICE COMPARISON (SIDE BY SIDE, CHEAPEST FLAGGED)",
    sepa_title: "SEPA PAYMENT RUN (PAIN.001.001.03 GERMAN DK STANDARD)",
    sepa_audit_trail: "PAYMENT RUN AUDIT TRAIL (WHICH FILE PAID WHICH INVOICES)",
    monthend_tax_title: "TAX SUMMARY: VAT BY RATE (19%, 7%, 0%) FOR TAX ADVISOR",
    monthend_checklist_title: "MONTH-END BOOKKEEPER CHECKLIST",
    settings_title: "COMPANY PROFILE, DATEV CHART OF ACCOUNTS & LOCALE",
    btn_save_settings: "SAVE SETTINGS",
    settings_company_hdr: "Company details",
    lbl_company_name: "Company name",
    lbl_company_address: "Company address",
    lbl_company_vat: "VAT ID",
    settings_datev_hdr: "DATEV export",
    settings_locale_hdr: "Language and formats",
    th_skonto_deadline: "Discount deadline",
    th_skonto_saving: "Discount saving",
    th_net_payable: "Net payable",
    th_payment_terms: "Payment terms",
    th_total_gross: "Total gross",
    chat_new_session: "New chat",
    upload_foreign_checkbox: "Auslandsrechnung / Foreign invoice",
    upload_foreign_hint: "Foreign currency & AI extraction (Groq)",
    fx_section_title: "Foreign Currency, ECB Conversion & Tax Treatment",
    lbl_supplier_country: "Supplier country (ISO)",
    lbl_source_currency: "Source currency",
    lbl_fx_rate: "FX rate (foreign per 1 EUR)",
    lbl_fx_date_source: "Rate date & source",
    lbl_tax_treatment: "Tax treatment",
    tt_standard: "Standard VAT (Regelbesteuerung)",
    tt_reverse_charge: "Reverse charge (Sec. 13b UStG)",
    tt_import_no_vat: "Import / non-EU (no VAT)",
    lbl_original_amounts: "Original amounts (source currency)",
    lbl_eur_amounts: "Converted EUR amounts",
    btn_convert_eur: "Convert to EUR",
    lbl_suggested_prefix: "Suggested:",
  },
};

const state = {
  activeView: "dashboard",
  lang: localStorage.getItem("autoinvoice_lang") || "de",
  sidebarCollapsed: localStorage.getItem("autoinvoice_sidebar_collapsed") === "true",
  user: null,
  userId: localStorage.getItem("invoice_app_user_id") || "default_user",
  conversationId: localStorage.getItem("invoice_app_conv_id") || ("conv_" + Math.random().toString(36).substring(2, 10)),
  invoices: [],
  vendorsData: { vendors: [], price_comparisons: [] },
  dashboardSummary: null,
  settings: {
    company_name: "Rheinwerk Anlagenbau AG",
    company_address: "Hafenstraße 88, 47119 Duisburg",
    vat_id: "DE119283746",
    tax_number: "109/5720/1842",
    iban: "DE89 3704 0044 0532 0130 00",
    bic: "COBADEFFXXX",
    leitweg_id: "",
    datev_chart: "SKR03",
    datev_consultant_number: "12345",
    datev_client_number: "40217",
    currency: "EUR",
    number_format: "1.234,56",
    date_format: "DD.MM.YYYY",
    language: "de",
  },
  currentDetailInvoice: null,
  pendingDeleteDocId: null,
  currentStatusFilter: "all",
  searchQuery: "",
  pendingEmail: "",
  resendCountdownTimer: null,
  resendSeconds: 60,
  selectedMonthEnd: "all",
};

function t(key) {
  const dict = I18N[state.lang] || I18N.de;
  return dict[key] || I18N.de[key] || key;
}

export function applyLanguage(lang) {
  state.lang = lang === "en" ? "en" : "de";
  localStorage.setItem("autoinvoice_lang", state.lang);
  document.documentElement.lang = state.lang;

  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const k = el.getAttribute("data-i18n");
    if (k) el.textContent = t(k);
  });

  const langBtn = document.getElementById("btn-lang-toggle");
  if (langBtn) {
    langBtn.innerHTML = state.lang === "de" ? "<strong>DE</strong> | EN" : "DE | <strong>EN</strong>";
  }

  const langSelect = document.getElementById("setting-language");
  if (langSelect) {
    langSelect.value = state.lang;
  }
}

function getSessionContext() {
  return {
    userId: state.user ? state.user.id : state.userId,
    conversationId: state.conversationId,
  };
}

function setConversationId(newId) {
  state.conversationId = newId;
  localStorage.setItem("invoice_app_conv_id", newId);
}

export function showToast(message, type = "info") {
  const container = document.getElementById("toast-container");
  if (!container) return;

  const toast = document.createElement("div");
  toast.className = `toast ${type}`;

  let stampLabel = state.lang === "en" ? "NOTICE" : "HINWEIS";
  if (type === "success") stampLabel = "OK";
  if (type === "error") stampLabel = state.lang === "en" ? "ALERT" : "FEHLER";

  toast.innerHTML = `
    <span class="stamp-badge ${type === "error" ? "failed" : (type === "success" ? "paid" : "pending")}" style="font-size:0.6rem; padding:0.1rem 0.35rem;">
      ${stampLabel}
    </span>
    <span>${escapeHtml(String(message || "").replace(/-/g, "-"))}</span>
  `;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(8px)";
    toast.style.transition = "all 0.25s ease";
    setTimeout(() => toast.remove(), 250);
  }, 4000);
}

// ─────────────────────────────────────────────────────────────────────────────
// App Initialization
// ─────────────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  applyLanguage(state.lang);
  setupCollapsibleSidebar();
  setupNavigation();
  setupDeleteModal();
  setupSearchAndFilters();
  setupAuth();
  setupCustomChartsModal();
  setupSettingsForm();
  setupVendorsAndPriceSearch();
  setupSepaPaymentRun();
  setupMonthEndView();

  initUpload({
    onUploadSuccess: refreshData,
    getSessionContext,
    showToast,
  });

  window.chatController = initChat({
    getSessionContext,
    setConversationId,
    showToast,
    onActionResolved: refreshData,
  });

  await checkAuthSession();
  await checkHealth();
  setInterval(checkHealth, 60000);
});

// ─────────────────────────────────────────────────────────────────────────────
// Task 3: Collapsible Sidebar + Mobile Drawer
// ─────────────────────────────────────────────────────────────────────────────
function setupCollapsibleSidebar() {
  const sidebar = document.getElementById("app-sidebar");
  const toggleBtn = document.getElementById("btn-sidebar-toggle");
  const chevron = document.getElementById("sidebar-toggle-chevron");
  const mobileBtn = document.getElementById("btn-mobile-menu");
  const backdrop = document.getElementById("sidebar-backdrop");
  const langToggleBtn = document.getElementById("btn-lang-toggle");

  function renderSidebarState() {
    if (!sidebar) return;
    sidebar.classList.toggle("collapsed", state.sidebarCollapsed);
    if (chevron) {
      chevron.setAttribute("points", state.sidebarCollapsed ? "9 18 15 12 9 6" : "15 18 9 12 15 6");
    }
  }

  renderSidebarState();

  if (toggleBtn) {
    toggleBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      state.sidebarCollapsed = !state.sidebarCollapsed;
      localStorage.setItem("autoinvoice_sidebar_collapsed", String(state.sidebarCollapsed));
      renderSidebarState();
    });
  }

  function closeMobileDrawer() {
    sidebar?.classList.remove("mobile-open");
    backdrop?.classList.remove("active");
  }

  if (mobileBtn) {
    mobileBtn.addEventListener("click", () => {
      sidebar?.classList.toggle("mobile-open");
      backdrop?.classList.toggle("active");
    });
  }

  if (backdrop) {
    backdrop.addEventListener("click", closeMobileDrawer);
  }

  if (langToggleBtn) {
    langToggleBtn.addEventListener("click", async () => {
      const nextLang = state.lang === "de" ? "en" : "de";
      applyLanguage(nextLang);
      renderRecentInvoices(state.invoices);
      renderLedgerTable(state.invoices);
      if (state.user) {
        try {
          await api.updateSettings({ ...state.settings, language: nextLang });
        } catch (err) {
          console.warn("Language save skipped:", err);
        }
      }
    });
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Authentication Folio Controller
// ─────────────────────────────────────────────────────────────────────────────
export function setAuthState(user) {
  state.user = user;
  const userBadgeContainer = document.getElementById("user-badge-container");
  const userEmailDisplay = document.getElementById("user-display-email");
  const authOverlay = document.getElementById("auth-overlay");
  const appRoot = document.getElementById("app-root");

  if (user) {
    state.userId = user.id;
    localStorage.setItem("invoice_app_user_id", user.id);
    if (userEmailDisplay) userEmailDisplay.textContent = user.email || "";
    if (userBadgeContainer) userBadgeContainer.style.display = "flex";
    if (authOverlay) authOverlay.classList.add("hidden");
    if (appRoot) {
      appRoot.style.display = "";
      appRoot.removeAttribute("inert");
      appRoot.removeAttribute("aria-hidden");
    }
  } else {
    state.userId = "default_user";
    localStorage.removeItem("invoice_app_user_id");
    if (userEmailDisplay) userEmailDisplay.textContent = "";
    if (userBadgeContainer) userBadgeContainer.style.display = "none";
    if (authOverlay) authOverlay.classList.remove("hidden");
    showAuthScreen("login");
    if (appRoot) {
      appRoot.style.display = "none";
      appRoot.setAttribute("inert", "");
      appRoot.setAttribute("aria-hidden", "true");
    }
  }
}

async function checkAuthSession() {
  try {
    const res = await api.getMe();
    if (res && res.status === "success" && res.user) {
      setAuthState(res.user);
      await loadUserSettings();
      await refreshData();
      return;
    }
  } catch (err) {
    console.warn("Session unauthenticated:", err);
  }
  setAuthState(null);
}

function showAuthScreen(screenName) {
  const screens = ["login", "signup", "verify", "forgot", "reset"];
  screens.forEach((s) => {
    const el = document.getElementById(`auth-screen-${s}`);
    if (el) el.style.display = s === screenName ? "block" : "none";
  });

  ["login-password", "signup-password", "reset-new-password"].forEach((id) => {
    const input = document.getElementById(id);
    if (input) input.value = "";
  });

  ["login-unverified-notice", "login-error-notice", "signup-error-notice", "verify-error-stamp", "verify-success-stamp", "forgot-notice", "reset-error-stamp"].forEach(hideNotice);

  if (screenName === "verify") {
    const emailEl = document.getElementById("verify-email-display");
    if (emailEl) emailEl.textContent = state.pendingEmail || "Ihre E-Mail";
    startResendCountdown(60);
    clearOtpBoxes();
    const firstBox = document.querySelector(".otp-digit[data-idx='0']");
    if (firstBox) firstBox.focus();
  }
}

function showNotice(id, text = "") {
  const el = document.getElementById(id);
  if (!el) return;
  if (text) {
    const desc = el.querySelector(".stamp-desc");
    if (desc) desc.textContent = text;
  }
  el.style.display = "flex";
}

function hideNotice(id) {
  const el = document.getElementById(id);
  if (el) el.style.display = "none";
}

function startResendCountdown(seconds = 60) {
  clearInterval(state.resendCountdownTimer);
  state.resendSeconds = seconds;

  const timerSpan = document.getElementById("resend-timer-seconds");
  const resendContainer = document.getElementById("resend-container");
  const resendBtn = document.getElementById("btn-trigger-resend");

  if (resendContainer) resendContainer.style.display = "inline";
  if (resendBtn) resendBtn.style.display = "none";
  if (timerSpan) timerSpan.textContent = state.resendSeconds;

  state.resendCountdownTimer = setInterval(() => {
    state.resendSeconds--;
    if (timerSpan) timerSpan.textContent = state.resendSeconds;
    if (state.resendSeconds <= 0) {
      clearInterval(state.resendCountdownTimer);
      if (resendContainer) resendContainer.style.display = "none";
      if (resendBtn) resendBtn.style.display = "inline";
    }
  }, 1000);
}

function clearOtpBoxes() {
  document.querySelectorAll(".otp-digit").forEach((box) => {
    box.value = "";
  });
}

function setupAuth() {
  document.getElementById("btn-goto-signup")?.addEventListener("click", () => showAuthScreen("signup"));
  document.getElementById("btn-goto-login")?.addEventListener("click", () => showAuthScreen("login"));
  document.getElementById("btn-goto-forgot")?.addEventListener("click", () => showAuthScreen("forgot"));
  document.getElementById("btn-verify-back-login")?.addEventListener("click", () => showAuthScreen("login"));
  document.getElementById("btn-forgot-back-login")?.addEventListener("click", () => showAuthScreen("login"));
  document.getElementById("btn-reset-back-login")?.addEventListener("click", () => showAuthScreen("login"));
  document.getElementById("btn-login-go-verify")?.addEventListener("click", () => showAuthScreen("verify"));

  document.getElementById("btn-logout")?.addEventListener("click", async () => {
    try {
      await api.logout();
      setAuthState(null);
      showToast("Abgemeldet.", "info");
    } catch (err) {
      setAuthState(null);
    }
  });

  window.addEventListener("session-expired", () => {
    setAuthState(null);
    showToast("Sitzung abgelaufen. Bitte erneut anmelden.", "info");
  });

  document.getElementById("form-login")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    hideNotice("login-error-notice");
    hideNotice("login-unverified-notice");

    const email = document.getElementById("login-email")?.value.trim() || "";
    const password = document.getElementById("login-password")?.value || "";

    try {
      const res = await api.login({ email, password });
      if (res.status === "success") {
        setAuthState(res.user);
        showToast("Anmeldung erfolgreich.", "success");
        await loadUserSettings();
        await refreshData();
      }
    } catch (err) {
      if (err.status === 403 && (err.data?.error === "unverified" || err.data?.detail?.error === "unverified")) {
        state.pendingEmail = email;
        showNotice("login-unverified-notice");
      } else {
        const msg = err.data?.error || err.data?.detail?.error || err.data?.detail || "Ungültige Anmeldedaten.";
        showNotice("login-error-notice", msg);
      }
    }
  });

  document.getElementById("form-signup")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    hideNotice("signup-error-notice");

    const email = document.getElementById("signup-email")?.value.trim() || "";
    const password = document.getElementById("signup-password")?.value || "";

    try {
      const res = await api.signup({ email, password });
      if (res.status === "success") {
        state.pendingEmail = email;
        showToast("Sicherheitscode gesendet.", "success");
        showAuthScreen("verify");
      }
    } catch (err) {
      const msg = err.data?.error || err.data?.detail?.error || err.data?.detail || "Registrierung fehlgeschlagen.";
      showNotice("signup-error-notice", msg);
    }
  });

  const otpBoxes = Array.from(document.querySelectorAll(".otp-digit"));
  otpBoxes.forEach((box, idx) => {
    box.addEventListener("input", (e) => {
      const val = e.target.value.replace(/\D/g, "");
      e.target.value = val ? val[val.length - 1] : "";
      if (e.target.value && idx < otpBoxes.length - 1) {
        otpBoxes[idx + 1].focus();
      }
    });
    box.addEventListener("keydown", (e) => {
      if (e.key === "Backspace" && !box.value && idx > 0) {
        otpBoxes[idx - 1].focus();
      }
    });
    box.addEventListener("paste", (e) => {
      e.preventDefault();
      const pasted = (e.clipboardData || window.clipboardData).getData("text") || "";
      const digits = pasted.replace(/\D/g, "").slice(0, 6);
      digits.split("").forEach((d, i) => {
        if (otpBoxes[i]) otpBoxes[i].value = d;
      });
      const targetFocus = Math.min(digits.length, otpBoxes.length - 1);
      if (otpBoxes[targetFocus]) otpBoxes[targetFocus].focus();
    });
  });

  document.getElementById("form-verify-otp")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    hideNotice("verify-error-stamp");

    const code = otpBoxes.map((b) => b.value.trim()).join("");
    if (code.length !== 6) {
      showNotice("verify-error-stamp", "Bitte den vollständigen 6-stelligen Code eingeben.");
      return;
    }

    try {
      const res = await api.verifyOtp({
        email: state.pendingEmail,
        code,
        purpose: "email_verification",
      });
      if (res.status === "success") {
        showToast("Konto erfolgreich bestätigt.", "success");
        const meRes = await api.getMe();
        setAuthState(meRes.user);
        await loadUserSettings();
        await refreshData();
      }
    } catch (err) {
      showNotice("verify-error-stamp", "Code ungültig oder abgelaufen.");
      clearOtpBoxes();
      if (otpBoxes[0]) otpBoxes[0].focus();
    }
  });

  document.getElementById("btn-trigger-resend")?.addEventListener("click", async () => {
    hideNotice("verify-error-stamp");
    hideNotice("verify-success-stamp");
    try {
      await api.resendOtp({ email: state.pendingEmail, purpose: "email_verification" });
      showNotice("verify-success-stamp", "Ein neuer Code wurde versendet.");
      startResendCountdown(60);
    } catch (err) {
      const remaining = err.data?.retry_after || 60;
      startResendCountdown(remaining);
      showNotice("verify-error-stamp", `Bitte ${remaining}s warten.`);
    }
  });

  document.getElementById("form-forgot")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = document.getElementById("forgot-email")?.value.trim() || "";
    state.pendingEmail = email;
    try {
      await api.forgotPassword({ email });
    } catch (err) {}
    showNotice("forgot-notice");
    setTimeout(() => showAuthScreen("reset"), 1200);
  });

  document.getElementById("form-reset")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    hideNotice("reset-error-stamp");
    const code = document.getElementById("reset-code")?.value.trim() || "";
    const newPassword = document.getElementById("reset-new-password")?.value || "";
    try {
      const res = await api.resetPassword({
        email: state.pendingEmail,
        code,
        new_password: newPassword,
      });
      if (res.status === "success") {
        showToast("Passwort aktualisiert.", "success");
        showAuthScreen("login");
      }
    } catch (err) {
      showNotice("reset-error-stamp", "Ungültiger Wiederherstellungscode.");
    }
  });

  document.querySelectorAll(".password-toggle-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      const wrapper = btn.closest(".password-input-wrapper");
      const input = wrapper?.querySelector("input");
      if (!input) return;
      const isPassword = input.type === "password";
      input.type = isPassword ? "text" : "password";
    });
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// View Router
// ─────────────────────────────────────────────────────────────────────────────
function setupNavigation() {
  const navItems = document.querySelectorAll(".nav-item[data-view]");
  const views = document.querySelectorAll(".view-section");
  const breadcrumb = document.getElementById("header-breadcrumb-title");
  const sidebar = document.getElementById("app-sidebar");
  const backdrop = document.getElementById("sidebar-backdrop");

  navItems.forEach((item) => {
    item.addEventListener("click", () => {
      const targetView = item.getAttribute("data-view");
      switchView(targetView);
      sidebar?.classList.remove("mobile-open");
      backdrop?.classList.remove("active");
    });
  });

  document.getElementById("btn-header-upload")?.addEventListener("click", () => switchView("upload"));
  document.getElementById("btn-dispatch-file-invoice")?.addEventListener("click", (e) => {
    e.preventDefault();
    switchView("upload");
  });

  window.switchView = switchView;
  window.openInvoiceDetail = openInvoiceDetail;

  function switchView(viewName) {
    state.activeView = viewName;

    navItems.forEach((it) => {
      it.classList.toggle("active", it.getAttribute("data-view") === viewName);
    });

    views.forEach((v) => {
      v.classList.toggle("active", v.id === `view-${viewName}`);
    });

    if (breadcrumb) {
      const titlesDE = {
        dashboard: "ÜBERSICHT, LIQUIDITÄT & SKONTO",
        upload: "RECHNUNGSEINGANG & 9-STUFEN-PRÜFUNG",
        ledger: "BELEGE (HAUPTBUCH & FTS5-SUCHE)",
        "invoice-detail": "BELEGDETAILS, KORREKTUR & XRECHNUNG",
        vendors: "LIEFERANTEN & ARTIKEL-PREISVERGLEICH",
        payments: "SEPA-ZAHLLAUF (PAIN.001.001.03)",
        monthend: "MONATSABSCHLUSS & UMSATZSTEUER",
        chat: "KI-ASSISTENT (SQL & QUELLE-ZITATE)",
        settings: "EINSTELLUNGEN (FIRMA, DATEV & LOCALE)",
      };
      const titlesEN = {
        dashboard: "DASHBOARD, CASH FLOW & SKONTO",
        upload: "INCOMING TRAY & 9-RULE VALIDATOR",
        ledger: "INVOICES LEDGER & FTS5 SEARCH",
        "invoice-detail": "INVOICE DETAIL, EDIT & XRECHNUNG",
        vendors: "VENDORS & ITEM PRICE COMPARISON",
        payments: "SEPA PAYMENT RUN (PAIN.001.001.03)",
        monthend: "MONTH-END CHECKLIST & VAT SUMMARY",
        chat: "AI COPILOT (SQL & CITATIONS)",
        settings: "SETTINGS (COMPANY, DATEV & LOCALE)",
      };
      const map = state.lang === "en" ? titlesEN : titlesDE;
      breadcrumb.textContent = map[viewName] || "HAUPTBUCH";
    }

    if (viewName === "dashboard") {
      setTimeout(() => charts.renderAll(state.invoices), 50);
    } else if (viewName === "vendors") {
      loadVendorsView();
    } else if (viewName === "payments") {
      renderSepaCandidates();
      loadSepaRuns();
    } else if (viewName === "monthend") {
      loadMonthEndData(state.selectedMonthEnd);
    }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Task 2: Settings Loading & Saving
// ─────────────────────────────────────────────────────────────────────────────
async function loadUserSettings() {
  try {
    const res = await api.getSettings();
    if (res.settings) {
      state.settings = res.settings;
      populateSettingsForm(state.settings);
      if (res.settings.language) {
        applyLanguage(res.settings.language);
      }
    }
  } catch (err) {
    console.warn("Could not load settings:", err);
  }
}

function populateSettingsForm(cfg) {
  const setVal = (id, val) => {
    const el = document.getElementById(id);
    if (el && val !== undefined && val !== null) el.value = val;
  };
  setVal("setting-company-name", cfg.company_name);
  setVal("setting-company-address", cfg.company_address);
  setVal("setting-vat-id", cfg.vat_id);
  setVal("setting-tax-number", cfg.tax_number);
  setVal("setting-iban", cfg.iban);
  setVal("setting-bic", cfg.bic);
  setVal("setting-leitweg-id", cfg.leitweg_id);
  setVal("setting-datev-chart", cfg.datev_chart || "SKR03");
  setVal("setting-datev-consultant", cfg.datev_consultant_number);
  setVal("setting-datev-client", cfg.datev_client_number);
  setVal("setting-language", cfg.language || state.lang);

  const compBadge = document.getElementById("header-company-badge");
  if (compBadge && cfg.company_name) {
    compBadge.textContent = cfg.company_name.toUpperCase();
  }
  const skrBadge = document.getElementById("sidebar-skr-badge");
  if (skrBadge && cfg.datev_chart) {
    skrBadge.textContent = cfg.datev_chart;
  }
}

function setupSettingsForm() {
  const form = document.getElementById("form-settings");
  if (!form) return;

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      company_name: document.getElementById("setting-company-name")?.value.trim(),
      company_address: document.getElementById("setting-company-address")?.value.trim(),
      vat_id: document.getElementById("setting-vat-id")?.value.trim(),
      tax_number: document.getElementById("setting-tax-number")?.value.trim(),
      iban: document.getElementById("setting-iban")?.value.trim(),
      bic: document.getElementById("setting-bic")?.value.trim(),
      leitweg_id: document.getElementById("setting-leitweg-id")?.value.trim(),
      datev_chart: document.getElementById("setting-datev-chart")?.value,
      datev_consultant_number: document.getElementById("setting-datev-consultant")?.value.trim(),
      datev_client_number: document.getElementById("setting-datev-client")?.value.trim(),
      language: document.getElementById("setting-language")?.value || "de",
    };

    try {
      const res = await api.updateSettings(payload);
      state.settings = res.settings || payload;
      populateSettingsForm(state.settings);
      applyLanguage(state.settings.language);
      showToast("Unternehmensprofil, DATEV- & Gebietsschema-Einstellungen gespeichert.", "success");
      await refreshData();
    } catch (err) {
      showToast(err.message || "Fehler beim Speichern der Einstellungen", "error");
    }
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Data Fetching & Dashboard Calculations
// ─────────────────────────────────────────────────────────────────────────────
async function refreshData() {
  try {
    const [invRes, dashRes] = await Promise.all([
      api.getInvoices(true),
      api.getDashboardSummary(),
    ]);
    state.invoices = invRes.invoices || [];
    state.dashboardSummary = dashRes;

    updateDashboardMetrics(state.invoices, dashRes);
    renderDashboardSummaryPanels(dashRes);
    charts.renderAll(state.invoices);
    renderRecentInvoices(state.invoices);
    renderLedgerTable(state.invoices);
    renderSepaCandidates();
    await loadCustomDashboardCharts();

    if (window.chatController && state.user) {
      window.chatController.loadConversations();
    }
  } catch (err) {
    if (err && (err.status === 401 || (err.message && err.message.includes("401")))) {
      setAuthState(null);
    } else {
      console.error("Refresh error:", err);
    }
  }
}

function updateDashboardMetrics(invoices, dashRes) {
  let totalSpend = 0;
  let unpaidCount = 0;
  const vendors = new Set();

  invoices.forEach((inv) => {
    const amt = parseFloat(inv.total_amount) || 0;
    totalSpend += amt;
    const vendor = cleanVendorName(inv.vendor_name);
    if (vendor && vendor !== "Unbekannter Lieferant") vendors.add(vendor);
    const st = (inv.payment_status || "").toLowerCase();
    if (!st.includes("paid") && !st.includes("bezahlt")) unpaidCount++;
  });

  const totalCount = invoices.length;
  const avgSpend = totalCount > 0 ? totalSpend / totalCount : 0;
  const healthPct = dashRes?.validation_health?.core_pass_rate_pct ?? 100;

  const setEl = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  };

  setEl("stat-total-spend", formatMoneyDE(totalSpend, "EUR"));
  setEl("stat-avg-spend", formatMoneyDE(avgSpend, "EUR"));
  setEl("stat-total-count", totalCount.toString());
  setEl("stat-vendors-count", `${vendors.size} ${state.lang === "en" ? "ACTIVE VENDORS" : "AKTIVE LIEFERANTEN"}`);
  setEl("stat-validation-health", `${formatNumberDE(healthPct, 0)}%`);
  setEl("stat-unpaid-count", `${unpaidCount} ${state.lang === "en" ? "OPEN / DUE" : "OFFENE BELEGE"}`);
  setEl("kpi-currency-label", "EUR (€)");
}

function renderDashboardSummaryPanels(dashRes) {
  if (!dashRes) return;

  // 1. Proactive Alerts (Tier 2)
  const alertsList = document.getElementById("dashboard-alerts-list");
  const alertsCount = document.getElementById("dashboard-alerts-count");
  const alerts = dashRes.proactive_alerts || [];
  if (alertsCount) {
    alertsCount.textContent = `${alerts.length} ${state.lang === "en" ? "ALERTS" : "HINWEISE"}`;
    alertsCount.className = `stamp-badge ${alerts.some((a) => a.severity === "critical" || a.severity === "high") ? "failed" : "pending"}`;
  }
  if (alertsList) {
    if (alerts.length === 0) {
      alertsList.innerHTML = `<div style="font-family:var(--font-mono); font-size:0.78rem; color:var(--stamp-green);">Keine kritischen Fristen oder Warnungen offen.</div>`;
    } else {
      alertsList.innerHTML = alerts
        .slice(0, 9)
        .map(
          (a) => `
          <div class="alert-slip ${escapeHtml(a.severity || "info")}" onclick="window.openInvoiceDetail('${escapeHtml(a.doc_id || a.invoice_number)}')">
            <div style="font-family:var(--font-mono); font-weight:700; font-size:0.74rem; margin-bottom:2px;">
              ${escapeHtml(a.title)}
            </div>
            <div style="color:var(--ink-muted); font-size:0.75rem;">
              ${escapeHtml(a.message)}
            </div>
          </div>
        `
        )
        .join("");
    }
  }

  // 2. Cash Flow & Skonto Panel (Task 8)
  const cfWeek = dashRes.cashflow?.due_this_week || { count: 0, total_amount: 0 };
  const cfMonth = dashRes.cashflow?.due_this_month || { count: 0, total_amount: 0 };
  const skonto = dashRes.skonto || { count: 0, total_savings: 0, opportunities: [] };

  const setEl = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  };

  setEl("cf-due-week-amount", formatMoneyDE(cfWeek.total_amount));
  setEl("cf-due-week-count", `${cfWeek.count} Beleg(e)`);
  setEl("cf-due-month-amount", formatMoneyDE(cfMonth.total_amount));
  setEl("cf-due-month-count", `${cfMonth.count} Beleg(e)`);
  setEl("cf-skonto-total", formatMoneyDE(skonto.total_savings));
  setEl("cf-skonto-count", `${skonto.count} Beleg(e) mit Skonto-Vorteil`);

  const skontoTbody = document.getElementById("dashboard-skonto-table-body");
  if (skontoTbody) {
    if (!skonto.opportunities || skonto.opportunities.length === 0) {
      skontoTbody.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:1rem; color:var(--ink-muted); font-family:var(--font-mono); font-size:0.78rem;">Keine offenen Rechnungen mit Skonto-Frist.</td></tr>`;
    } else {
      skontoTbody.innerHTML = skonto.opportunities
        .map(
          (op) => `
          <tr>
            <td><button type="button" class="inv-link" onclick="window.openInvoiceDetail('${escapeHtml(op.doc_id || op.invoice_number)}')">${escapeHtml(op.invoice_number)}</button></td>
            <td>${escapeHtml(op.vendor_name)}</td>
            <td style="font-family:var(--font-mono); font-size:0.78rem;">${escapeHtml(op.skonto_deadline_de)} (${formatNumberDE(op.skonto_percent, 0)}%)</td>
            <td class="mono-amount" style="color:var(--stamp-green);">-${formatMoneyDE(op.skonto_saving)}</td>
            <td class="mono-amount">${formatMoneyDE(op.net_payable)}</td>
          </tr>
        `
        )
        .join("");
    }
  }

  // 3. Validation Health & Top Failure Reasons (Tier 2)
  const vhBody = document.getElementById("dashboard-validation-health-body");
  if (vhBody && dashRes.validation_health) {
    const vh = dashRes.validation_health;
    const reasons = vh.top_failure_reasons || [];
    const reasonsHtml =
      reasons.length === 0
        ? `<div style="font-family:var(--font-mono); font-size:0.76rem; color:var(--stamp-green);">Alle Belege haben sämtliche Prüfungen bestanden.</div>`
        : reasons
            .map(
              (r) => `
            <div style="display:flex; justify-content:space-between; align-items:center; padding:0.45rem 0.6rem; background:var(--paper); border:1px solid var(--hairline); font-family:var(--font-mono); font-size:0.74rem;">
              <span>${escapeHtml(r.reason)}</span>
              <span class="stamp-badge failed">${r.count}x</span>
            </div>
          `
            )
            .join("");

    vhBody.innerHTML = `
      <div style="display:flex; justify-content:space-between; padding:0.6rem 0.75rem; background:var(--paper); border:1px solid var(--ink);">
        <div>
          <div class="field-label">KERNPRÜFUNG (V1-V8 RECHENWERK &amp; § 14 USTG)</div>
          <div style="font-family:var(--font-serif); font-size:1.35rem; font-weight:700; color:var(--stamp-green);">
            ${formatNumberDE(vh.core_pass_rate_pct, 1)}% (${vh.core_pass_count}/${vh.total_invoices} Belege)
          </div>
        </div>
        <div style="text-align:right;">
          <div class="field-label">INKL. LEITWEG-ID (V9)</div>
          <div style="font-family:var(--font-mono); font-size:1.05rem; font-weight:700;">
            ${formatNumberDE(vh.full_pass_rate_pct, 1)}%
          </div>
        </div>
      </div>
      <div class="field-label" style="margin-top:0.35rem;">TOP-FEHLERGRÜNDE:</div>
      ${reasonsHtml}
    `;
  }
}

function getStatusBadgeHtml(status) {
  const st = (status || "").toLowerCase();
  if ((st.includes("paid") && !st.includes("unpaid")) || st.includes("bezahlt")) {
    return `<span class="stamp-badge paid">${state.lang === "en" ? "PAID" : "BEZAHLT"}</span>`;
  }
  if (st.includes("overdue") || st.includes("überfällig")) {
    return `<span class="stamp-badge overdue">${state.lang === "en" ? "OVERDUE" : "ÜBERFÄLLIG"}</span>`;
  }
  return `<span class="stamp-badge pending">${state.lang === "en" ? "OPEN" : "OFFEN"}</span>`;
}

function getValidationBadgeHtml(inv) {
  const vr = inv.validation_report || {};
  const st = inv.validation_status || vr.overall_status || "PASS";
  if (st === "FAIL") {
    return `<span class="stamp-badge failed" title="Rechen- oder Pflichtfeldfehler">FAIL</span>`;
  }
  if (st === "WARN") {
    return `<span class="stamp-badge pending" title="V1-V8 OK, V9 Leitweg-ID fehlt">V1-V8 OK (V9 FEHLT)</span>`;
  }
  return `<span class="stamp-badge paid">PASS (9/9)</span>`;
}

function renderRecentInvoices(invoices) {
  const tbody = document.getElementById("dashboard-recent-table-body");
  if (!tbody) return;

  const recent = [...invoices].slice(0, 6);
  if (recent.length === 0) {
    tbody.innerHTML = `
      <tr><td colspan="7" style="text-align:center; padding:1.5rem; color:var(--ink-muted); font-family:var(--font-mono); font-size:0.8rem;">
        NOCH KEINE BELEGE IN SQLITE ERFASST
      </td></tr>
    `;
    return;
  }

  tbody.innerHTML = recent
    .map((inv) => {
      const docId = inv.doc_id || inv._doc_id || inv.invoice_number;
      const invNum = inv.invoice_number || "N/A";
      const vendor = cleanVendorName(inv.vendor_name);
      const ibanWarn = inv.iban_change_flag ? `<span class="stamp-badge failed" style="margin-left:6px;">IBAN-WARNUNG!</span>` : "";
      return `
        <tr>
          <td>
            <button type="button" class="inv-link" onclick="window.openInvoiceDetail('${escapeHtml(docId)}')">${escapeHtml(invNum)}</button>
            ${ibanWarn}
          </td>
          <td>${escapeHtml(vendor)}</td>
          <td style="font-family:var(--font-mono); font-size:0.8rem;">${formatDateDE(inv.invoice_date)}</td>
          <td style="font-family:var(--font-mono); font-size:0.8rem;">${formatDateDE(inv.due_date)}</td>
          <td class="mono-amount" style="text-align:right;">${formatMoneyDE(inv.total_amount, inv.currency)}</td>
          <td style="text-align:center;">${getValidationBadgeHtml(inv)}</td>
          <td style="text-align:right;">${getStatusBadgeHtml(inv.payment_status)}</td>
        </tr>
      `;
    })
    .join("");
}

function renderLedgerTable(invoices) {
  const tbody = document.getElementById("ledger-table-body");
  const filedEl = document.getElementById("ledger-filtered-count");
  if (!tbody) return;

  let filtered = [...invoices];

  if (state.currentStatusFilter !== "all") {
    filtered = filtered.filter((inv) => {
      const st = (inv.payment_status || "").toLowerCase();
      const valSt = (inv.validation_status || "").toUpperCase();
      if (state.currentStatusFilter === "paid") return (st.includes("paid") && !st.includes("unpaid")) || st.includes("bezahlt");
      if (state.currentStatusFilter === "unpaid") return !st.includes("paid") || st.includes("unpaid");
      if (state.currentStatusFilter === "failed") return valSt === "FAIL" || valSt === "WARN";
      return true;
    });
  }

  if (filedEl) {
    filedEl.textContent = `${filtered.length} ${state.lang === "en" ? "ENTRIES" : "BELEGE"}`;
  }

  if (filtered.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="11" style="text-align:center; padding:3rem 1rem; color:var(--ink-muted); font-family:var(--font-mono); font-size:0.82rem;">
          [ KEINE BELEGE GEFUNDEN ]
        </td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = filtered
    .map((inv) => {
      const docId = inv.doc_id || inv._doc_id || inv.invoice_number;
      const invNum = inv.invoice_number || "N/A";
      const vendor = cleanVendorName(inv.vendor_name);
      const flags = [];
      if (inv.document_type === "credit_note") flags.push(`<span class="stamp-badge pending" style="font-size:0.58rem;">GUTSCHRIFT</span>`);
      if (inv.iban_change_flag) flags.push(`<span class="stamp-badge failed" style="font-size:0.58rem;">IBAN-BETRUGSWARNUNG</span>`);
      if (inv.duplicate_flag) flags.push(`<span class="stamp-badge failed" style="font-size:0.58rem;">DUPLIKAT?</span>`);

      return `
        <tr>
          <td>
            <button type="button" class="inv-link" onclick="window.openInvoiceDetail('${escapeHtml(docId)}')">${escapeHtml(invNum)}</button>
            ${flags.length ? `<div style="margin-top:3px; display:flex; gap:4px; flex-wrap:wrap;">${flags.join("")}</div>` : ""}
          </td>
          <td style="font-weight:600;">${escapeHtml(vendor)}</td>
          <td style="font-family:var(--font-mono); font-size:0.75rem;">${escapeHtml(inv.vat_id || "-")}</td>
          <td style="font-family:var(--font-mono); font-size:0.75rem;">${formatDateDE(inv.invoice_date)}</td>
          <td style="font-family:var(--font-mono); font-size:0.75rem;">${formatDateDE(inv.due_date)}</td>
          <td class="mono-amount">${formatMoneyDE(inv.subtotal, inv.currency)}</td>
          <td class="mono-amount">${formatMoneyDE(inv.tax_amount, inv.currency)}</td>
          <td class="mono-amount" style="font-weight:700;">${formatMoneyDE(inv.total_amount, inv.currency)}</td>
          <td style="text-align:center;">${getValidationBadgeHtml(inv)}</td>
          <td style="text-align:center;">${getStatusBadgeHtml(inv.payment_status)}</td>
          <td style="text-align:right; white-space:nowrap;">
            <button type="button" class="btn-secondary" style="padding:0.25rem 0.5rem; font-size:0.7rem;" onclick="window.openInvoiceDetail('${escapeHtml(docId)}')">
              Details / XRechnung
            </button>
            <button class="btn-delete-row btn-action-delete" data-doc-id="${escapeHtml(docId)}" data-inv-num="${escapeHtml(invNum)}" title="Beleg löschen">
              &times;
            </button>
          </td>
        </tr>
      `;
    })
    .join("");

  tbody.querySelectorAll(".btn-action-delete").forEach((btn) => {
    btn.addEventListener("click", () => {
      openDeleteModal(btn.getAttribute("data-doc-id"), btn.getAttribute("data-inv-num"));
    });
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Task 4, 5, 6, 9, 11: Per-Invoice Detail Page (Editable + 9-Rule Report + XRechnung)
// ─────────────────────────────────────────────────────────────────────────────
export async function openInvoiceDetail(identifier) {
  if (!identifier) return;
  window.switchView("invoice-detail");
  const container = document.getElementById("invoice-detail-container");
  if (!container) return;

  container.innerHTML = `<div style="padding:2rem; text-align:center; font-family:var(--font-mono);">Lade Belegdetails &amp; 9-Stufen-Prüfbericht für ${escapeHtml(identifier)}...</div>`;

  try {
    const res = await api.getInvoiceDetail(identifier);
    const inv = res.invoice;
    state.currentDetailInvoice = inv;
    renderInvoiceDetailView(inv);
  } catch (err) {
    container.innerHTML = `<div style="padding:2rem; color:var(--stamp-red); font-family:var(--font-mono);">Fehler: ${escapeHtml(err.message)}</div>`;
  }
}

const EU_NON_DE_SET = new Set([
  "AT", "BE", "BG", "CY", "CZ", "DK", "EE", "ES", "FI", "FR",
  "GR", "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL",
  "PL", "PT", "RO", "SE", "SI", "SK",
]);

function suggestTaxTreatmentJS(countryCode) {
  const cc = String(countryCode || "").trim().toUpperCase();
  if (!cc || cc === "DE") return "standard";
  if (EU_NON_DE_SET.has(cc)) return "reverse_charge_13b";
  return "import_no_vat";
}

function renderInvoiceDetailView(inv) {
  const container = document.getElementById("invoice-detail-container");
  if (!container || !inv) return;

  const docId = inv.doc_id || inv._doc_id || inv.invoice_number;
  const vr = inv.validation_report || {};
  const checks = vr.checks || [];
  const items = inv.line_items || [];
  const vatBreakdown = inv.vat_breakdown || [];

  // Task 11: Loud IBAN Change Fraud Alert Banner
  const ibanBanner = inv.iban_change_flag
    ? `
    <div class="fraud-alert-banner">
      <div>
        <span class="stamp-badge failed">BETRUGSSIGNAL / IBAN-ÄNDERUNG ERKANNT</span>
        <h3 style="font-family:var(--font-serif); font-size:1.1rem; color:var(--stamp-red); margin-top:0.35rem;">
          Abweichende Bankverbindung für ${escapeHtml(inv.vendor_name)}!
        </h3>
        <p style="font-family:var(--font-mono); font-size:0.78rem; margin-top:0.2rem;">
          Gespeicherte Stamm-IBAN: <strong>${escapeHtml(inv.previous_iban)}</strong> &bull;
          IBAN auf diesem Beleg: <strong style="color:var(--stamp-red);">${escapeHtml(inv.vendor_iban)}</strong>
        </p>
      </div>
      <button type="button" class="btn-secondary" id="btn-acknowledge-iban">Neue IBAN als legitim bestätigen</button>
    </div>
  `
    : "";

  const dupBanner = inv.duplicate_flag
    ? `
    <div class="fraud-alert-banner" style="border-color:var(--stamp-amber); background:#FBF4E6; box-shadow:3px 3px 0 var(--stamp-amber);">
      <div>
        <span class="stamp-badge pending">DUPLIKATWARNUNG (TIER 2)</span>
        <p style="font-family:var(--font-mono); font-size:0.8rem; margin-top:0.3rem;">
          Mögliches Duplikat zu Beleg <strong>${escapeHtml(inv.duplicate_of)}</strong> (gleicher Lieferant, gleicher Bruttobetrag, nahes Datum).
        </p>
      </div>
    </div>
  `
    : "";

  const viesBadgeClass = inv.vies_status === "valid" ? "paid" : (inv.vies_status === "invalid" ? "failed" : "pending");
  const viesLabel = state.lang === "en"
    ? (inv.vies_status === "valid" ? "VIES VALID" : (inv.vies_status === "invalid" ? "VIES INVALID" : "VIES UNCHECKED"))
    : (inv.vies_status === "valid" ? "VIES GÜLTIG" : (inv.vies_status === "invalid" ? "VIES UNGÜLTIG" : "VIES UNGEPRÜFT"));
  const viesTime = inv.vies_checked_at ? formatDateDE(inv.vies_checked_at.slice(0, 10)) : "-";

  const itemsRowsHtml = items
    .map((it, idx) => {
      const lineSt = it.validation_status || "PASS";
      const isFail = lineSt === "FAIL";
      return `
        <tr class="detail-line-row" data-idx="${idx}" style="${isFail ? "background:#FDF0EE;" : ""}">
          <td><input type="text" class="detail-input ln-pos" value="${escapeHtml(it.pos || idx + 1)}" style="width:48px;"></td>
          <td><input type="text" class="detail-input ln-art" value="${escapeHtml(it.article_number || "")}" style="width:95px;"></td>
          <td><input type="text" class="detail-input ln-desc" value="${escapeHtml(it.description || "")}" style="min-width:210px;"></td>
          <td><input type="text" inputmode="decimal" class="detail-input ln-qty" value="${it.quantity ?? 1}" style="width:76px; text-align:right;"></td>
          <td><input type="text" class="detail-input ln-unit" value="${escapeHtml(it.unit || "Stk")}" style="width:56px;"></td>
          <td><input type="text" inputmode="decimal" class="detail-input ln-pe" value="${it.price_unit ?? 1}" style="width:60px; text-align:right;"></td>
          <td><input type="text" inputmode="decimal" class="detail-input ln-price" value="${it.unit_price ?? 0}" style="width:92px; text-align:right;"></td>
          <td><input type="text" inputmode="decimal" class="detail-input ln-disc" value="${it.discount_percent ?? 0}" style="width:64px; text-align:right;"></td>
          <td><input type="text" inputmode="decimal" class="detail-input ln-total" value="${it.line_total ?? 0}" style="width:105px; text-align:right; font-weight:700;"></td>
          <td><input type="text" inputmode="decimal" class="detail-input ln-vat" value="${it.vat_rate ?? 19}" style="width:62px; text-align:right;"></td>
          <td style="min-width:190px;">
            <span class="stamp-badge ${isFail ? "failed" : "paid"}">${lineSt}</span>
            ${
              isFail
                ? `<div style="font-family:var(--font-mono); font-size:0.7rem; color:var(--stamp-red); margin-top:4px;">
                     Erw.: ${formatMoneyDE(it.expected_total)} | Gedr.: ${formatMoneyDE(it.line_total)} | Diff.: ${formatMoneyDE(it.difference)}
                   </div>`
                : `<span style="font-family:var(--font-mono); font-size:0.7rem; color:var(--ink-muted); margin-left:4px;">${formatMoneyDE(it.line_total)}</span>`
            }
          </td>
        </tr>
      `;
    })
    .join("");

  const vatRowsHtml = (vatBreakdown.length ? vatBreakdown : [{ rate: 19, net: inv.subtotal, vat: inv.tax_amount }])
    .map(
      (vb, i) => `
      <tr class="detail-vat-row" data-idx="${i}">
        <td><input type="text" inputmode="decimal" class="detail-input vb-rate" value="${vb.rate ?? 19}" style="width:80px;"> %</td>
        <td><input type="text" inputmode="decimal" class="detail-input vb-net" value="${vb.net ?? 0}" style="width:140px; text-align:right;"> € (${formatMoneyDE(vb.net)})</td>
        <td><input type="text" inputmode="decimal" class="detail-input vb-vat" value="${vb.vat ?? 0}" style="width:140px; text-align:right;"> € (${formatMoneyDE(vb.vat)})</td>
        <td class="mono-amount">${formatMoneyDE((parseFloat(vb.net) || 0) + (parseFloat(vb.vat) || 0))}</td>
      </tr>
    `
    )
    .join("");

  const checksHtml = checks
    .map((c) => {
      const st = c.status || "PASS";
      const badgeCls = st === "PASS" ? "paid" : (st === "FAIL" ? "failed" : "pending");
      const discs = c.discrepancies || [];
      const discHtml = discs.length
        ? `<div style="margin-top:0.45rem; padding:0.45rem 0.65rem; background:var(--paper); border:1px solid var(--stamp-red); font-family:var(--font-mono); font-size:0.74rem;">
            ${discs
              .map(
                (d) =>
                  `<div><strong>Position / Feld: ${escapeHtml(d.line || "-")}</strong> &bull; Erwartet: <strong>${typeof d.expected === "number" ? formatMoneyDE(d.expected) : escapeHtml(d.expected)}</strong> &bull; Gedruckt: <strong>${typeof d.printed === "number" ? formatMoneyDE(d.printed) : escapeHtml(d.printed)}</strong> &bull; Differenz: <strong style="color:var(--stamp-red);">${typeof d.difference === "number" ? formatMoneyDE(d.difference) : escapeHtml(d.difference)}</strong></div>`
              )
              .join("")}
          </div>`
        : "";
      return `
        <div class="validation-check-row ${st === "FAIL" ? "fail-row" : ""}">
          <div style="flex:1;">
            <div style="font-family:var(--font-mono); font-weight:700; font-size:0.8rem;">
              ${escapeHtml(c.code)} - ${escapeHtml(state.lang === "en" && c.name_en ? c.name_en : c.name)}
            </div>
            <div style="font-size:0.8rem; color:var(--ink-muted); margin-top:2px;">
              ${escapeHtml(c.message)}
            </div>
            ${discHtml}
          </div>
          <span class="stamp-badge ${badgeCls}">${escapeHtml(st)}</span>
        </div>
      `;
    })
    .join("");

  container.innerHTML = `
    ${ibanBanner}
    ${dupBanner}

    <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.75rem; border-bottom:2px solid var(--ink); padding-bottom:1rem; margin-bottom:1.25rem;">
      <div>
        <div style="display:flex; align-items:center; gap:0.6rem;">
          <button type="button" class="btn-secondary" onclick="window.switchView('ledger')" style="padding:0.3rem 0.65rem; font-size:0.74rem;">&larr; Zurück zu Belege</button>
          <span class="stamp-badge ${vr.overall_status === "FAIL" ? "failed" : (vr.overall_status === "WARN" ? "pending" : "paid")}">
            PRÜFSTATUS: ${escapeHtml(vr.overall_status || "PASS")} (${vr.passed_count || 0}/9 OK)
          </span>
          <span class="stamp-badge ${viesBadgeClass}" title="${escapeHtml(inv.vies_details || "")}">
            ${viesLabel} (${viesTime})
          </span>
        </div>
        <h2 style="font-family:var(--font-serif); font-size:1.55rem; margin-top:0.5rem;">
          Beleg ${escapeHtml(inv.invoice_number)} &bull; ${escapeHtml(inv.vendor_name)}
        </h2>
      </div>

      <div style="display:flex; gap:0.6rem; flex-wrap:wrap;">
        <button type="button" class="btn-secondary" id="btn-detail-vies-recheck" style="font-size:0.75rem;">
          VIES &amp; Prüfung aktualisieren
        </button>
        <button type="button" class="btn-secondary" id="btn-detail-fill-leitweg" style="font-size:0.75rem;">
          Standard-Leitweg-ID übernehmen
        </button>
        <button type="button" class="btn-secondary" id="btn-detail-download-xrechnung" style="font-size:0.75rem; border-color:var(--stamp-green); color:var(--stamp-green);">
          XRechnung XML (EN 16931) herunterladen
        </button>
        <button type="button" class="btn-primary" id="btn-detail-save" style="font-size:0.75rem;">
          ÄNDERUNGEN SPEICHERN
        </button>
      </div>
    </div>

    <!-- Header Fields Grid -->
    <div class="field-grid" style="grid-template-columns: repeat(4, 1fr); margin-bottom:1.25rem;">
      <div class="field-item">
        <div class="field-label">RECHNUNGSNUMMER</div>
        <input type="text" id="dt-invoice-number" class="detail-input" value="${escapeHtml(inv.invoice_number)}">
      </div>
      <div class="field-item">
        <div class="field-label">BELEGART</div>
        <select id="dt-document-type" class="detail-input">
          <option value="invoice" ${inv.document_type !== "credit_note" ? "selected" : ""}>Rechnung (380)</option>
          <option value="credit_note" ${inv.document_type === "credit_note" ? "selected" : ""}>Gutschrift (381)</option>
        </select>
      </div>
      <div class="field-item">
        <div class="field-label">ZAHLUNGSSTATUS</div>
        <select id="dt-payment-status" class="detail-input">
          <option value="Unpaid" ${inv.payment_status !== "Paid" ? "selected" : ""}>Offen (Unpaid)</option>
          <option value="Paid" ${inv.payment_status === "Paid" ? "selected" : ""}>Bezahlt (Paid)</option>
        </select>
      </div>
      <div class="field-item" style="${!inv.leitweg_id ? "border-color:var(--stamp-amber); background:#FBF4E6;" : ""}">
        <div class="field-label">LEITWEG-ID (BT-10 PFLICHT FÜR XRECHNUNG)</div>
        <input type="text" id="dt-leitweg-id" class="detail-input" placeholder="z.B. 04011000-1234512345-06" value="${escapeHtml(inv.leitweg_id || "")}">
      </div>

      <div class="field-item">
        <div class="field-label">LIEFERANT (NAME)</div>
        <input type="text" id="dt-vendor-name" class="detail-input" value="${escapeHtml(inv.vendor_name)}">
      </div>
      <div class="field-item">
        <div class="field-label">LIEFERANTENANSCHRIFT</div>
        <input type="text" id="dt-vendor-address" class="detail-input" value="${escapeHtml(inv.vendor_address)}">
      </div>
      <div class="field-item">
        <div class="field-label">LIEFERANT UST-IDNR. (V5 / VIES)</div>
        <input type="text" id="dt-vat-id" class="detail-input" value="${escapeHtml(inv.vat_id)}">
      </div>
      <div class="field-item">
        <div class="field-label">STEUERNUMMER</div>
        <input type="text" id="dt-tax-number" class="detail-input" value="${escapeHtml(inv.tax_number)}">
      </div>

      <div class="field-item">
        <div class="field-label">LIEFERANTEN-IBAN</div>
        <input type="text" id="dt-vendor-iban" class="detail-input" value="${escapeHtml(inv.vendor_iban)}">
      </div>
      <div class="field-item">
        <div class="field-label">LIEFERANTEN-BIC</div>
        <input type="text" id="dt-vendor-bic" class="detail-input" value="${escapeHtml(inv.vendor_bic)}">
      </div>
      <div class="field-item">
        <div class="field-label">KÄUFER (NAME)</div>
        <input type="text" id="dt-buyer-name" class="detail-input" value="${escapeHtml(inv.buyer_name)}">
      </div>
      <div class="field-item">
        <div class="field-label">KÄUFERANSCHRIFT</div>
        <input type="text" id="dt-buyer-address" class="detail-input" value="${escapeHtml(inv.buyer_address)}">
      </div>

      <div class="field-item">
        <div class="field-label">BELEGDATUM (${formatDateDE(inv.invoice_date)})</div>
        <input type="text" id="dt-invoice-date" class="detail-input" value="${escapeHtml(inv.invoice_date)}" placeholder="YYYY-MM-DD oder TT.MM.JJJJ">
      </div>
      <div class="field-item">
        <div class="field-label">LEISTUNGSDATUM (${formatDateDE(inv.delivery_date)})</div>
        <input type="text" id="dt-delivery-date" class="detail-input" value="${escapeHtml(inv.delivery_date)}">
      </div>
      <div class="field-item">
        <div class="field-label">FÄLLIGKEITSDATUM (${formatDateDE(inv.due_date)})</div>
        <input type="text" id="dt-due-date" class="detail-input" value="${escapeHtml(inv.due_date)}">
      </div>
      <div class="field-item">
        <div class="field-label">ZAHLUNGSBEDINGUNGEN</div>
        <input type="text" id="dt-payment-terms" class="detail-input" value="${escapeHtml(inv.payment_terms)}">
      </div>

      <div class="field-item" style="grid-column: span 2;">
        <div class="field-label">SKONTO-BEDINGUNGEN</div>
        <input type="text" id="dt-skonto-terms" class="detail-input" value="${escapeHtml(inv.skonto_terms)}">
      </div>
      <div class="field-item">
        <div class="field-label">SKONTO % / FRIST (${formatDateDE(inv.skonto_deadline)})</div>
        <div style="display:flex; gap:0.4rem;">
          <input type="text" inputmode="decimal" id="dt-skonto-percent" class="detail-input" value="${inv.skonto_percent ?? 0}" style="width:70px;" title="Skonto %">
          <input type="text" id="dt-skonto-deadline" class="detail-input" value="${escapeHtml(inv.skonto_deadline || "")}" placeholder="YYYY-MM-DD">
        </div>
      </div>
      <div class="field-item">
        <div class="field-label">SKONTO-ERSPARNIS (EUR)</div>
        <input type="text" inputmode="decimal" id="dt-skonto-amount" class="detail-input" value="${inv.skonto_amount ?? 0}">
      </div>
    </div>

    <!-- Foreign Currency, FX Conversion & Tax Treatment Panel (Tasks 3, 4, 7) -->
    ${(() => {
      const srcCcy = (inv.source_currency || inv.currency || "EUR").toUpperCase();
      const supCountry = (inv.supplier_country || "").toUpperCase();
      const suggestedTt = suggestTaxTreatmentJS(supCountry);
      const activeTt = inv.tax_treatment || suggestedTt;
      const fxRate = Number(inv.fx_rate || 1.0);
      const fxDate = inv.fx_rate_date || "-";
      const fxSource = inv.fx_rate_source || "-";
      const origGross = Number(inv.original_gross || (srcCcy !== "EUR" ? inv.total_amount : 0) || 0);
      const origNet = Number(inv.original_net || (srcCcy !== "EUR" ? inv.subtotal : 0) || 0);
      const origVat = Number(inv.original_vat || (srcCcy !== "EUR" ? inv.tax_amount : 0) || 0);
      const suggestedLabel = suggestedTt === "reverse_charge_13b" ? t("tt_reverse_charge") : (suggestedTt === "import_no_vat" ? t("tt_import_no_vat") : t("tt_standard"));
      return `
      <div style="border:1px solid var(--hairline); background:var(--paper-raised); padding:1rem; margin-bottom:1.25rem;">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem; margin-bottom:0.75rem;">
          <h3 class="settings-section-heading" style="margin:0; border:none;">${t("fx_section_title")}</h3>
          <button type="button" class="btn-primary" id="btn-detail-convert-eur" style="font-size:0.75rem;">${t("btn_convert_eur")}</button>
        </div>
        <div class="field-grid" style="grid-template-columns: repeat(4, 1fr);">
          <div class="field-item">
            <div class="field-label">${t("lbl_supplier_country")}</div>
            <input type="text" id="dt-supplier-country" class="detail-input" maxlength="2" placeholder="DE / PL / IN" value="${escapeHtml(supCountry)}">
          </div>
          <div class="field-item">
            <div class="field-label">${t("lbl_source_currency")}</div>
            <input type="text" id="dt-source-currency" class="detail-input" maxlength="3" value="${escapeHtml(srcCcy)}">
          </div>
          <div class="field-item">
            <div class="field-label">${t("lbl_fx_rate")}</div>
            <input type="text" inputmode="decimal" id="dt-fx-rate" class="detail-input" placeholder="ECB auto or manual" value="${fxRate !== 1.0 ? fxRate : ""}">
          </div>
          <div class="field-item">
            <div class="field-label">${t("lbl_fx_date_source")}</div>
            <div style="font-family:var(--font-mono); font-size:0.8rem; padding-top:4px;">
              <strong>${escapeHtml(fxSource)}</strong> &bull; ${escapeHtml(fxDate === "-" ? "-" : formatDateDE(fxDate))}
            </div>
          </div>
          <div class="field-item" style="grid-column: span 2;">
            <div class="field-label">${t("lbl_tax_treatment")} <span style="color:var(--ink-muted); font-weight:400;">(${t("lbl_suggested_prefix")} ${escapeHtml(suggestedLabel)})</span></div>
            <select id="dt-tax-treatment" class="detail-input">
              <option value="standard" ${activeTt === "standard" ? "selected" : ""}>${t("tt_standard")}</option>
              <option value="reverse_charge_13b" ${activeTt === "reverse_charge_13b" ? "selected" : ""}>${t("tt_reverse_charge")}</option>
              <option value="import_no_vat" ${activeTt === "import_no_vat" ? "selected" : ""}>${t("tt_import_no_vat")}</option>
            </select>
          </div>
          <div class="field-item" style="grid-column: span 2;">
            <div class="field-label">${t("lbl_original_amounts")} &rarr; ${t("lbl_eur_amounts")}</div>
            <div style="font-family:var(--font-mono); font-size:0.78rem; padding-top:4px;">
              ${srcCcy !== "EUR" || origGross > 0
                ? `<strong>${formatNumberDE(origNet)} / ${formatNumberDE(origVat)} / ${formatNumberDE(origGross)} ${escapeHtml(srcCcy)}</strong> &rarr; <strong>${formatMoneyDE(inv.subtotal)} / ${formatMoneyDE(inv.tax_amount)} / ${formatMoneyDE(inv.total_amount)}</strong>`
                : `<span>${formatMoneyDE(inv.subtotal)} / ${formatMoneyDE(inv.tax_amount)} / ${formatMoneyDE(inv.total_amount)} (EUR)</span>`
              }
            </div>
          </div>
        </div>
      </div>
      `;
    })()}

    <!-- Line Items Table with Line-Level Validation Flags -->
    <div style="margin-bottom:1.5rem;">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.5rem;">
        <h3 class="settings-section-heading" style="margin:0; border:none;">Rechnungspositionen &amp; Positionsprüfung (V1)</h3>
      </div>
      <div class="data-table-wrapper">
        <table class="data-table">
          <thead>
            <tr>
              <th>POS.</th>
              <th>ART.-NR.</th>
              <th>BEZEICHNUNG</th>
              <th class="th-amount">MENGE</th>
              <th>ME</th>
              <th class="th-amount">PE</th>
              <th class="th-amount">EINZELPREIS (€)</th>
              <th class="th-amount">RAB. %</th>
              <th class="th-amount">GESAMT (€)</th>
              <th class="th-amount">UST %</th>
              <th>PRÜFFLAGGE (V1)</th>
            </tr>
          </thead>
          <tbody id="detail-lines-tbody">
            ${itemsRowsHtml}
          </tbody>
        </table>
      </div>
    </div>

    <!-- VAT Breakdown & Totals Row -->
    <div style="display:grid; grid-template-columns: 7fr 5fr; gap:1.25rem; margin-bottom:1.5rem;">
      <div>
        <h3 class="settings-section-heading">Umsatzsteuer-Aufschlüsselung je Steuersatz (V2 &amp; V3)</h3>
        <div class="data-table-wrapper">
          <table class="data-table">
            <thead>
              <tr>
                <th>STEUERSATZ</th>
                <th>NETTOBETRAG (€)</th>
                <th>UST-BETRAG (€)</th>
                <th class="th-amount">BRUTTO JE SATZ</th>
              </tr>
            </thead>
            <tbody id="detail-vat-tbody">
              ${vatRowsHtml}
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <h3 class="settings-section-heading">Belegsummen (V4: Netto + USt = Brutto)</h3>
        <div class="field-grid" style="grid-template-columns: 1fr;">
          <div class="field-item" style="display:flex; justify-content:space-between; align-items:center;">
            <span class="field-label" style="margin:0;">NETTOBETRAG GESAMT (${formatMoneyDE(inv.subtotal)})</span>
            <input type="text" inputmode="decimal" id="dt-subtotal" class="detail-input" value="${inv.subtotal ?? 0}" style="width:150px; text-align:right; font-weight:700;">
          </div>
          <div class="field-item" style="display:flex; justify-content:space-between; align-items:center;">
            <span class="field-label" style="margin:0;">UMSATZSTEUER GESAMT (${formatMoneyDE(inv.tax_amount)})</span>
            <input type="text" inputmode="decimal" id="dt-tax-amount" class="detail-input" value="${inv.tax_amount ?? 0}" style="width:150px; text-align:right; font-weight:700;">
          </div>
          <div class="field-item" style="display:flex; justify-content:space-between; align-items:center; border-color:var(--ink);">
            <span class="field-label" style="margin:0; color:var(--ink);">BRUTTOBETRAG GESAMT (${formatMoneyDE(inv.total_amount)})</span>
            <input type="text" inputmode="decimal" id="dt-total-amount" class="detail-input" value="${inv.total_amount ?? 0}" style="width:150px; text-align:right; font-weight:700; font-size:1rem;">
          </div>
        </div>
      </div>
    </div>

    <!-- 9-Check Validation Report (Task 5) -->
    <div>
      <h3 class="settings-section-heading">9-Stufen-Prüfbericht (Deterministische Python-Prüfung V1 bis V9)</h3>
      <div style="border:1px solid var(--ink);">
        ${checksHtml}
      </div>
    </div>
  `;

  // Bind Detail Page Actions
  document.getElementById("btn-detail-save")?.addEventListener("click", () => saveInvoiceDetailChanges(docId));

  document.getElementById("btn-detail-convert-eur")?.addEventListener("click", async () => {
    const rawRate = document.getElementById("dt-fx-rate")?.value.trim() || "";
    let manualRate = null;
    if (rawRate !== "") {
      manualRate = parseGermanDecimal(rawRate);
      if (manualRate === null || manualRate <= 0) {
        showToast(`Ungültiger Wechselkurs: "${rawRate}"`, "error");
        return;
      }
    }
    const taxTreatment = document.getElementById("dt-tax-treatment")?.value || "standard";
    const sourceCurrency = document.getElementById("dt-source-currency")?.value.trim().toUpperCase() || inv.source_currency;
    try {
      const res = await api.convertInvoiceToEur(docId, { fxRate: manualRate, taxTreatment, sourceCurrency });
      state.currentDetailInvoice = res.invoice;
      renderInvoiceDetailView(res.invoice);
      await refreshData();
      showToast(res.message || "In EUR umgerechnet.", "success");
    } catch (err) {
      showToast(err.message || "Umrechnung fehlgeschlagen", "error");
    }
  });

  document.getElementById("btn-detail-vies-recheck")?.addEventListener("click", async () => {
    try {
      showToast("Prüfe USt-IdNr. über EU VIES & aktualisiere 9-Stufen-Bericht...", "info");
      const res = await api.revalidateInvoice(docId);
      state.currentDetailInvoice = res.invoice;
      renderInvoiceDetailView(res.invoice);
      await refreshData();
      showToast("Prüfbericht & VIES-Status aktualisiert.", "success");
    } catch (err) {
      showToast(err.message || "Fehler bei der Prüfung", "error");
    }
  });

  document.getElementById("btn-detail-fill-leitweg")?.addEventListener("click", async () => {
    const lwInput = document.getElementById("dt-leitweg-id");
    const defaultLw = (state.settings?.leitweg_id || "").trim();
    if (!defaultLw) {
      showToast("Keine Standard-Leitweg-ID in den Einstellungen hinterlegt. Bitte erst in den Einstellungen speichern.", "error");
      return;
    }
    if (lwInput) lwInput.value = defaultLw;
    await saveInvoiceDetailChanges(docId);
  });

  document.getElementById("btn-detail-download-xrechnung")?.addEventListener("click", async () => {
    const lwVal = document.getElementById("dt-leitweg-id")?.value.trim() || inv.leitweg_id || "";
    if (!lwVal) {
      showToast(
        "V9 Hinweis: Leitweg-ID (BT-10) fehlt! Bitte klicken Sie auf 'Standard-Leitweg-ID übernehmen' oder tragen Sie eine Leitweg-ID ein, um die XRechnung herunterzuladen.",
        "error"
      );
      return;
    }
    if (lwVal !== inv.leitweg_id) {
      await saveInvoiceDetailChanges(docId);
    }
    const url = api.getXRechnungDownloadUrl(docId, lwVal, false);
    window.open(url, "_blank");
    showToast(`XRechnung XML (EN 16931) für ${inv.invoice_number} wird heruntergeladen.`, "success");
  });

  document.getElementById("btn-acknowledge-iban")?.addEventListener("click", async () => {
    try {
      const res = await api.updateInvoice(docId, { acknowledge_iban_change: true });
      state.currentDetailInvoice = res.invoice;
      renderInvoiceDetailView(res.invoice);
      await refreshData();
      showToast("Neue Lieferanten-IBAN als legitim bestätigt.", "success");
    } catch (err) {
      showToast(err.message || "Fehler", "error");
    }
  });
}

async function saveInvoiceDetailChanges(docId) {
  const parseFieldStrict = (rawVal, fieldLabel) => {
    const val = parseGermanDecimal(rawVal);
    if (val === null) {
      throw new Error(`Ungültiger Zahlenwert im Feld "${fieldLabel}": "${rawVal}"`);
    }
    return val;
  };

  let updatedItems = [];
  let updatedVat = [];
  let skontoPercent = 0;
  let skontoAmount = 0;
  let subtotalVal = 0;
  let taxAmountVal = 0;
  let totalAmountVal = 0;

  try {
    const lineRows = Array.from(document.querySelectorAll(".detail-line-row"));
    updatedItems = lineRows.map((tr, idx) => {
      const pos = tr.querySelector(".ln-pos")?.value.trim() || String(idx + 1);
      return {
        pos,
        article_number: tr.querySelector(".ln-art")?.value.trim() || "",
        description: tr.querySelector(".ln-desc")?.value.trim() || "",
        quantity: parseFieldStrict(tr.querySelector(".ln-qty")?.value, `Position ${pos} Menge (quantity)`),
        unit: tr.querySelector(".ln-unit")?.value.trim() || "Stk",
        price_unit: parseFieldStrict(tr.querySelector(".ln-pe")?.value, `Position ${pos} Preiseinheit (price_unit)`),
        unit_price: parseFieldStrict(tr.querySelector(".ln-price")?.value, `Position ${pos} Einzelpreis (unit_price)`),
        discount_percent: parseFieldStrict(tr.querySelector(".ln-disc")?.value, `Position ${pos} Rabatt % (discount_percent)`),
        line_total: parseFieldStrict(tr.querySelector(".ln-total")?.value, `Position ${pos} Gesamtbetrag (line_total)`),
        vat_rate: parseFieldStrict(tr.querySelector(".ln-vat")?.value, `Position ${pos} USt % (vat_rate)`),
      };
    });

    const vatRows = Array.from(document.querySelectorAll(".detail-vat-row"));
    updatedVat = vatRows.map((tr, idx) => ({
      rate: parseFieldStrict(tr.querySelector(".vb-rate")?.value, `USt-Aufschlüsselung Zeile ${idx + 1} Steuersatz (rate)`),
      net: parseFieldStrict(tr.querySelector(".vb-net")?.value, `USt-Aufschlüsselung Zeile ${idx + 1} Nettobetrag (net)`),
      vat: parseFieldStrict(tr.querySelector(".vb-vat")?.value, `USt-Aufschlüsselung Zeile ${idx + 1} USt-Betrag (vat)`),
    }));

    skontoPercent = parseFieldStrict(document.getElementById("dt-skonto-percent")?.value, "Skonto % (skonto_percent)");
    skontoAmount = parseFieldStrict(document.getElementById("dt-skonto-amount")?.value, "Skonto-Ersparnis (skonto_amount)");
    subtotalVal = parseFieldStrict(document.getElementById("dt-subtotal")?.value, "Nettobetrag Gesamt (subtotal)");
    taxAmountVal = parseFieldStrict(document.getElementById("dt-tax-amount")?.value, "Umsatzsteuer Gesamt (tax_amount)");
    totalAmountVal = parseFieldStrict(document.getElementById("dt-total-amount")?.value, "Bruttobetrag Gesamt (total_amount)");
  } catch (validationErr) {
    showToast(validationErr.message, "error");
    return false;
  }

  const payload = {
    invoice_number: document.getElementById("dt-invoice-number")?.value.trim(),
    document_type: document.getElementById("dt-document-type")?.value,
    payment_status: document.getElementById("dt-payment-status")?.value,
    leitweg_id: document.getElementById("dt-leitweg-id")?.value.trim(),
    vendor_name: document.getElementById("dt-vendor-name")?.value.trim(),
    vendor_address: document.getElementById("dt-vendor-address")?.value.trim(),
    vat_id: document.getElementById("dt-vat-id")?.value.trim(),
    tax_number: document.getElementById("dt-tax-number")?.value.trim(),
    vendor_iban: document.getElementById("dt-vendor-iban")?.value.trim(),
    vendor_bic: document.getElementById("dt-vendor-bic")?.value.trim(),
    buyer_name: document.getElementById("dt-buyer-name")?.value.trim(),
    buyer_address: document.getElementById("dt-buyer-address")?.value.trim(),
    supplier_country: document.getElementById("dt-supplier-country")?.value.trim().toUpperCase(),
    source_currency: document.getElementById("dt-source-currency")?.value.trim().toUpperCase(),
    tax_treatment: document.getElementById("dt-tax-treatment")?.value,
    invoice_date: document.getElementById("dt-invoice-date")?.value.trim(),
    delivery_date: document.getElementById("dt-delivery-date")?.value.trim(),
    due_date: document.getElementById("dt-due-date")?.value.trim(),
    payment_terms: document.getElementById("dt-payment-terms")?.value.trim(),
    skonto_terms: document.getElementById("dt-skonto-terms")?.value.trim(),
    skonto_percent: skontoPercent,
    skonto_deadline: document.getElementById("dt-skonto-deadline")?.value.trim(),
    skonto_amount: skontoAmount,
    subtotal: subtotalVal,
    tax_amount: taxAmountVal,
    total_amount: totalAmountVal,
    line_items: updatedItems,
    vat_breakdown: updatedVat,
  };

  try {
    const res = await api.updateInvoice(docId, payload);
    state.currentDetailInvoice = res.invoice;
    renderInvoiceDetailView(res.invoice);
    await refreshData();
    showToast("Beleg in SQLite gespeichert und 9-Stufen-Prüfung aktualisiert.", "success");
    return true;
  } catch (err) {
    showToast(err.message || "Fehler beim Speichern", "error");
    return false;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Task 7 & 11 & Tier 2: Vendors Page & Item Price Comparison
// ─────────────────────────────────────────────────────────────────────────────
async function loadVendorsView(itemQuery = "") {
  try {
    const res = await api.getVendors(itemQuery);
    state.vendorsData = res;
    renderVendorsTable(res.vendors || []);
    renderPriceComparisonTable(res.price_comparisons || []);
  } catch (err) {
    console.error("Failed to load vendors:", err);
  }
}

function renderVendorsTable(vendors) {
  const tbody = document.getElementById("vendors-table-body");
  const countBadge = document.getElementById("vendors-summary-count");
  if (countBadge) countBadge.textContent = `${vendors.length} LIEFERANT(EN)`;
  if (!tbody) return;

  if (vendors.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding:2rem; color:var(--ink-muted);">Noch keine Lieferanten in SQLite vorhanden.</td></tr>`;
    return;
  }

  tbody.innerHTML = vendors
    .map((v, idx) => {
      const viesCls = v.vies_status === "valid" ? "paid" : (v.vies_status === "invalid" ? "failed" : "pending");
      const ibanWarn = v.iban_change_detected
        ? `<div style="margin-top:3px;"><span class="stamp-badge failed">WARNUNG: IBAN GEÄNDERT!</span></div>`
        : "";
      const scoreCls = v.validation_score_pct >= 90 ? "paid" : (v.validation_score_pct >= 70 ? "pending" : "failed");

      return `
        <tr class="vendor-row" data-idx="${idx}" style="cursor:pointer;">
          <td><strong>${escapeHtml(v.vendor_name)}</strong><div style="font-size:0.74rem; color:var(--ink-muted);">${escapeHtml(v.address)}</div></td>
          <td style="font-family:var(--font-mono); font-size:0.76rem;">
            <div>${escapeHtml(v.vat_id || "-")}</div>
            <span class="stamp-badge ${viesCls}" style="font-size:0.58rem; margin-top:2px;">VIES: ${escapeHtml(v.vies_status)}</span>
          </td>
          <td style="font-family:var(--font-mono); font-size:0.75rem;">
            <div>${escapeHtml(v.iban || "-")}</div>
            <div style="color:var(--ink-muted);">BIC: ${escapeHtml(v.bic || "-")}</div>
            ${ibanWarn}
          </td>
          <td style="font-size:0.78rem;">${escapeHtml(v.payment_terms || "-")}</td>
          <td style="text-align:center; font-family:var(--font-mono); font-weight:700;">${v.invoice_count}</td>
          <td class="mono-amount">${formatMoneyDE(v.average_invoice_value)}</td>
          <td class="mono-amount" style="font-weight:700;">${formatMoneyDE(v.total_spend)}</td>
          <td style="text-align:center;">
            <span class="stamp-badge ${scoreCls}">${formatNumberDE(v.validation_score_pct, 1)}% (${v.passed_core_count}/${v.invoice_count})</span>
          </td>
        </tr>
      `;
    })
    .join("");

  tbody.querySelectorAll(".vendor-row").forEach((tr) => {
    tr.addEventListener("click", () => {
      const idx = parseInt(tr.getAttribute("data-idx"), 10);
      const v = vendors[idx];
      if (v) showVendorDrilldown(v);
    });
  });

  if (vendors.length > 0) {
    showVendorDrilldown(vendors[0]);
  }
}

function showVendorDrilldown(vendor) {
  const panel = document.getElementById("vendor-drilldown-panel");
  const titleEl = document.getElementById("vendor-drilldown-title");
  const ibanEl = document.getElementById("vendor-drilldown-iban");
  const ibanInput = document.getElementById("vendor-edit-iban-input");
  const btnSaveIban = document.getElementById("btn-save-vendor-iban");
  const tbody = document.getElementById("vendor-drilldown-tbody");
  if (!panel || !tbody) return;

  panel.style.display = "block";
  if (titleEl) titleEl.textContent = `Belege von ${vendor.vendor_name} (${vendor.invoice_count})`;
  if (ibanEl) ibanEl.textContent = `USt-IdNr.: ${vendor.vat_id || "-"}`;
  if (ibanInput) ibanInput.value = vendor.iban || "";

  if (btnSaveIban) {
    btnSaveIban.onclick = async () => {
      try {
        await api.updateVendor(vendor.vendor_name, { iban: ibanInput.value.trim() });
        showToast(`Stamm-IBAN für ${vendor.vendor_name} aktualisiert (IBAN-Änderungsprüfung neu berechnet).`, "success");
        await refreshData();
        await loadVendorsView();
      } catch (err) {
        showToast(err.message || "Fehler", "error");
      }
    };
  }

  tbody.innerHTML = (vendor.invoices || [])
    .map(
      (inv) => `
      <tr>
        <td><button type="button" class="inv-link" onclick="window.openInvoiceDetail('${escapeHtml(inv.doc_id || inv.invoice_number)}')">${escapeHtml(inv.invoice_number)}</button></td>
        <td>${inv.document_type === "credit_note" ? "Gutschrift" : "Rechnung"}</td>
        <td style="font-family:var(--font-mono); font-size:0.75rem;">${formatDateDE(inv.invoice_date)}</td>
        <td style="font-family:var(--font-mono); font-size:0.75rem;">${formatDateDE(inv.due_date)}</td>
        <td style="font-family:var(--font-mono); font-size:0.75rem;">
          ${escapeHtml(inv.vendor_iban || "-")}
          ${inv.iban_change_flag ? `<span class="stamp-badge failed" style="margin-left:4px;">ABWEICHEND!</span>` : ""}
        </td>
        <td class="mono-amount">${formatMoneyDE(inv.subtotal)}</td>
        <td class="mono-amount" style="font-weight:700;">${formatMoneyDE(inv.total_amount)}</td>
        <td style="text-align:center;">${getValidationBadgeHtml(inv)}</td>
        <td style="text-align:right;">${getStatusBadgeHtml(inv.payment_status)}</td>
      </tr>
    `
    )
    .join("");
}

function renderPriceComparisonTable(comparisons) {
  const tbody = document.getElementById("price-comparison-tbody");
  if (!tbody) return;

  if (!comparisons || comparisons.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:2rem; color:var(--ink-muted);">Keine Artikelpositionen für den Preisvergleich gefunden.</td></tr>`;
    return;
  }

  const rows = [];
  comparisons.forEach((item) => {
    (item.offers || []).forEach((o, idx) => {
      const badge = o.is_cheapest
        ? `<span class="stamp-badge paid">GÜNSTIGSTER PREIS</span>`
        : `<span class="stamp-badge pending">+${formatNumberDE(o.price_spread_pct, 1)}%</span>`;
      rows.push(`
        <tr>
          <td>${idx === 0 ? `<strong>${escapeHtml(item.article_number)}</strong> &bull; ${escapeHtml(item.description)}` : `<span style="color:var(--ink-muted); padding-left:1rem;">↳ ${escapeHtml(item.article_number)}</span>`}</td>
          <td>
            <strong>${escapeHtml(o.vendor_name)}</strong>
            (<button type="button" class="inv-link" onclick="window.openInvoiceDetail('${escapeHtml(o.doc_id || o.invoice_number)}')">${escapeHtml(o.invoice_number)}</button>)
          </td>
          <td style="font-family:var(--font-mono); font-size:0.76rem;">${formatDateDE(o.invoice_date)}</td>
          <td class="mono-amount">${formatMoneyDE(o.unit_price)}</td>
          <td style="text-align:center; font-family:var(--font-mono); font-size:0.76rem;">${formatNumberDE(o.price_unit, 0)} ${escapeHtml(o.unit)}</td>
          <td class="mono-amount" style="font-weight:700;">${formatNumberDE(o.effective_unit_price, 3)} € / ${escapeHtml(o.unit)}</td>
          <td style="text-align:right;">${badge}</td>
        </tr>
      `);
    });
  });

  tbody.innerHTML = rows.join("");
}

function setupVendorsAndPriceSearch() {
  const searchInput = document.getElementById("price-comparison-search");
  if (!searchInput) return;
  searchInput.addEventListener("input", (e) => {
    loadVendorsView(e.target.value.trim());
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Task 10: SEPA Payment Run (pain.001.001.03) & Audit Trail
// ─────────────────────────────────────────────────────────────────────────────
function renderSepaCandidates() {
  const tbody = document.getElementById("sepa-candidates-tbody");
  if (!tbody) return;

  const openInvs = state.invoices.filter(
    (inv) =>
      strLower(inv.payment_status) !== "paid" &&
      strLower(inv.payment_status) !== "bezahlt" &&
      inv.document_type !== "credit_note" &&
      (parseFloat(inv.total_amount) || 0) > 0
  );

  if (openInvs.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding:2rem; color:var(--ink-muted);">Keine offenen Rechnungen für einen Zahllauf vorhanden.</td></tr>`;
    return;
  }

  tbody.innerHTML = openInvs
    .map((inv) => {
      const docId = inv.doc_id || inv._doc_id || inv.invoice_number;
      const isCoreValid = inv.validation_report?.core_math_valid !== false;
      return `
        <tr>
          <td><input type="checkbox" class="sepa-inv-checkbox" value="${escapeHtml(inv.invoice_number)}" ${isCoreValid ? "checked" : ""}></td>
          <td><button type="button" class="inv-link" onclick="window.openInvoiceDetail('${escapeHtml(docId)}')">${escapeHtml(inv.invoice_number)}</button></td>
          <td><strong>${escapeHtml(inv.vendor_name)}</strong></td>
          <td style="font-family:var(--font-mono); font-size:0.76rem;">${formatDateDE(inv.due_date)}</td>
          <td style="font-family:var(--font-mono); font-size:0.75rem;">
            ${escapeHtml(inv.vendor_iban || "FEHLT")} &bull; ${escapeHtml(inv.vendor_bic || "-")}
            ${inv.iban_change_flag ? `<span class="stamp-badge failed" style="margin-left:4px;">IBAN GEÄNDERT!</span>` : ""}
          </td>
          <td style="text-align:center;">${getValidationBadgeHtml(inv)}</td>
          <td class="mono-amount" style="font-weight:700;">${formatMoneyDE(inv.total_amount)}</td>
          <td class="mono-amount" style="color:var(--stamp-green);">${inv.skonto_amount > 0 ? `-${formatMoneyDE(inv.skonto_amount)} (${formatNumberDE(inv.skonto_percent, 0)}%)` : "-"}</td>
        </tr>
      `;
    })
    .join("");
}

function setupSepaPaymentRun() {
  const selectAll = document.getElementById("sepa-select-all");
  const btnPreview = document.getElementById("btn-sepa-preview");
  const btnCancel = document.getElementById("btn-sepa-cancel-preview");
  const btnConfirm = document.getElementById("btn-sepa-confirm-execute");
  const panel = document.getElementById("sepa-approval-panel");

  if (selectAll) {
    selectAll.addEventListener("change", () => {
      document.querySelectorAll(".sepa-inv-checkbox").forEach((cb) => {
        cb.checked = selectAll.checked;
      });
    });
  }

  let currentSelectedIds = [];

  if (btnPreview) {
    btnPreview.addEventListener("click", async () => {
      const checked = Array.from(document.querySelectorAll(".sepa-inv-checkbox:checked")).map((cb) => cb.value);
      if (checked.length === 0) {
        showToast("Bitte mindestens eine Rechnung auswählen.", "error");
        return;
      }
      currentSelectedIds = checked;
      const applySkonto = Boolean(document.getElementById("sepa-apply-skonto")?.checked);

      try {
        const res = await api.previewSepaRun(checked, applySkonto);
        const pv = res.preview;
        if (panel) panel.style.display = "block";
        const totEl = document.getElementById("sepa-preview-total");
        if (totEl) totEl.textContent = formatMoneyDE(pv.control_sum);

        const detailsEl = document.getElementById("sepa-preview-details");
        if (detailsEl) {
          const validRows = (pv.valid_payments || [])
            .map(
              (tx) =>
                `<div style="padding:0.3rem 0; border-bottom:1px dashed var(--hairline);">
                  ✓ <strong>${escapeHtml(tx.invoice_number)}</strong> (${escapeHtml(tx.vendor_name)}) &rarr; IBAN: ${escapeHtml(tx.iban)} (Mod-97 OK) &bull; Betrag: <strong>${formatMoneyDE(tx.amount)}</strong>
                </div>`
            )
            .join("");

          const blockedRows = (pv.blocked_payments || [])
            .map(
              (b) =>
                `<div style="padding:0.35rem 0.5rem; margin-top:4px; background:#FDF0EE; border:1px solid var(--stamp-red); color:var(--stamp-red);">
                  ✕ BLOCKIERT: <strong>${escapeHtml(b.invoice_number)}</strong> (${escapeHtml(b.vendor_name)}) &bull; Grund: ${escapeHtml(b.reason)}
                </div>`
            )
            .join("");

          detailsEl.innerHTML = `
            <div style="margin-bottom:0.5rem;">
              <strong>Auftraggeber (Debitor aus Einstellungen):</strong> ${escapeHtml(pv.debtor.name)} &bull; IBAN: ${escapeHtml(pv.debtor.iban)} &bull; BIC: ${escapeHtml(pv.debtor.bic)}
            </div>
            <div><strong>Gültige Einzeltransaktionen (${pv.tx_count}):</strong></div>
            ${validRows || "<div>Keine gültigen Transaktionen.</div>"}
            ${blockedRows ? `<div style="margin-top:0.5rem;"><strong>Blockierte Zahlungen (blockieren nicht den restlichen Lauf):</strong>${blockedRows}</div>` : ""}
          `;
        }
      } catch (err) {
        showToast(err.message || "Fehler bei der SEPA-Vorschau", "error");
      }
    });
  }

  if (btnCancel) {
    btnCancel.addEventListener("click", () => {
      if (panel) panel.style.display = "none";
    });
  }

  if (btnConfirm) {
    btnConfirm.addEventListener("click", async () => {
      const applySkonto = Boolean(document.getElementById("sepa-apply-skonto")?.checked);
      try {
        const res = await api.executeSepaRun({
          invoiceIds: currentSelectedIds,
          applySkonto,
          humanApproved: true,
        });
        if (panel) panel.style.display = "none";
        showToast(res.message || "SEPA pain.001.001.03 erstellt!", "success");
        const runId = res.payment_run?.run_id;
        if (runId) {
          window.open(api.getSepaRunDownloadUrl(runId), "_blank");
        }
        await refreshData();
        await loadSepaRuns();
      } catch (err) {
        showToast(err.message || "SEPA-Ausführung fehlgeschlagen", "error");
      }
    });
  }
}

async function loadSepaRuns() {
  const tbody = document.getElementById("sepa-runs-tbody");
  if (!tbody) return;
  try {
    const res = await api.getSepaRuns();
    const runs = res.runs || [];
    if (runs.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:1.5rem; color:var(--ink-muted);">Noch keine SEPA-Zahlläufe im Prüfpfad protokolliert.</td></tr>`;
      return;
    }
    tbody.innerHTML = runs
      .map((r) => {
        const paidList = (r.paid_invoices || []).map((p) => p.invoice_number).join(", ");
        const blockedList = (r.blocked_invoices || []).map((b) => `${b.invoice_number} (blockiert)`).join(", ");
        const dlUrl = api.getSepaRunDownloadUrl(r.run_id);
        return `
          <tr>
            <td style="font-family:var(--font-mono); font-weight:700;">${escapeHtml(r.message_id)}<div style="font-size:0.7rem; color:var(--ink-muted);">${escapeHtml(r.file_name)}</div></td>
            <td style="font-family:var(--font-mono); font-size:0.76rem;">${formatDateDE(r.created_at.slice(0, 10))}</td>
            <td style="font-family:var(--font-mono); font-size:0.76rem;">${escapeHtml(r.approved_by || "-")}</td>
            <td style="text-align:center; font-family:var(--font-mono); font-weight:700;">${r.tx_count}</td>
            <td class="mono-amount" style="font-weight:700;">${formatMoneyDE(r.control_sum)}</td>
            <td style="font-family:var(--font-mono); font-size:0.75rem;">
              <div>Bezahlt: <strong>${escapeHtml(paidList || "-")}</strong></div>
              ${blockedList ? `<div style="color:var(--stamp-red);">Blockiert: ${escapeHtml(blockedList)}</div>` : ""}
            </td>
            <td style="text-align:right;">
              <a href="${dlUrl}" class="btn-primary" style="text-decoration:none; padding:0.3rem 0.65rem; font-size:0.72rem;">
                pain.001 XML ↓
              </a>
            </td>
          </tr>
        `;
      })
      .join("");
  } catch (err) {
    console.warn("Could not load SEPA runs:", err);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Tier 2: Month-End Checklist & Tax Summary Page
// ─────────────────────────────────────────────────────────────────────────────
function setupMonthEndView() {
  const selectEl = document.getElementById("monthend-month-select");
  if (!selectEl) return;
  selectEl.addEventListener("change", () => {
    state.selectedMonthEnd = selectEl.value;
    const expBtn = document.getElementById("btn-datev-csv-export");
    if (expBtn) expBtn.href = api.getDatevExportUrl(state.selectedMonthEnd);
    loadMonthEndData(state.selectedMonthEnd);
  });
}

async function loadMonthEndData(month = "all") {
  try {
    const res = await api.getMonthEnd(month);
    renderTaxSummaryTable(res.tax_summary);
    renderMonthEndChecklist(res.checklist, res.selected_month);
  } catch (err) {
    console.error("Failed to load month-end:", err);
  }
}

function renderTaxSummaryTable(taxSummary) {
  const tbody = document.getElementById("tax-summary-tbody");
  if (!tbody || !taxSummary) return;

  const rates = taxSummary.rates || [];
  const rowsHtml = rates
    .map(
      (r) => `
      <tr>
        <td style="font-family:var(--font-mono); font-weight:700;">${r.rate}% USt</td>
        <td style="font-family:var(--font-mono); font-size:0.78rem;">${escapeHtml(r.datev_account)}</td>
        <td style="font-family:var(--font-mono); font-size:0.78rem;">${escapeHtml(r.expense_account)}</td>
        <td style="text-align:center; font-family:var(--font-mono);">${r.invoice_count}</td>
        <td class="mono-amount">${formatMoneyDE(r.net_total)}</td>
        <td class="mono-amount">${formatMoneyDE(r.vat_total)}</td>
        <td class="mono-amount" style="font-weight:700;">${formatMoneyDE(r.gross_total)}</td>
      </tr>
    `
    )
    .join("");

  tbody.innerHTML =
    rowsHtml +
    `
    <tr style="border-top:2px solid var(--ink); background:var(--paper);">
      <td colspan="4" style="font-family:var(--font-mono); font-weight:700;">SUMME FÜR UMSATZSTEUER-VORANMELDUNG / STEUERBERATER</td>
      <td class="mono-amount" style="font-weight:700;">${formatMoneyDE(taxSummary.total_net)}</td>
      <td class="mono-amount" style="font-weight:700;">${formatMoneyDE(taxSummary.total_vat)}</td>
      <td class="mono-amount" style="font-weight:700;">${formatMoneyDE(taxSummary.total_gross)}</td>
    </tr>
  `;
}

function renderMonthEndChecklist(checklist, selectedMonth) {
  const container = document.getElementById("monthend-checklist-container");
  const badge = document.getElementById("monthend-progress-badge");
  if (!container || !checklist) return;

  const sections = [
    { key: "unbooked", title: "1. Unverbuchte Belege (DATEV-Übergabe offen)", items: checklist.unbooked || [], markBooked: true },
    { key: "failed_validation", title: "2. Fehlgeschlagene Prüfungen (V1-V8 Abweichungen)", items: checklist.failed_validation || [], markBooked: false },
    { key: "missing_vat_id", title: "3. Fehlende USt-IdNr.", items: checklist.missing_vat_id || [], markBooked: false },
    { key: "missing_leitweg_id", title: "4. Fehlende Leitweg-ID (für XRechnung BT-10)", items: checklist.missing_leitweg_id || [], markBooked: false },
  ];

  let totalItems = 0;
  let checkedItems = 0;
  sections.forEach((s) => {
    totalItems += s.items.length;
    checkedItems += s.items.filter((i) => i.checked).length;
  });

  if (badge) {
    badge.textContent = `${checkedItems} / ${totalItems} ERLEDIGT`;
  }

  container.innerHTML = sections
    .map((sec) => {
      const itemsHtml =
        sec.items.length === 0
          ? `<div style="font-family:var(--font-mono); font-size:0.76rem; color:var(--stamp-green); padding:0.5rem 0;">✓ Keine offenen Punkte in dieser Kategorie.</div>`
          : sec.items
              .map(
                (it) => `
              <div class="checklist-item-row">
                <label style="display:flex; align-items:center; gap:0.5rem; cursor:pointer; flex:1;">
                  <input type="checkbox" class="monthend-check-input" data-item-key="${escapeHtml(it.item_key)}" data-mark-booked="${sec.markBooked}" ${it.checked ? "checked" : ""}>
                  <span style="${it.checked ? "text-decoration:line-through; color:var(--ink-muted);" : ""}">${escapeHtml(it.label)}</span>
                </label>
                <button type="button" class="inv-link" onclick="window.openInvoiceDetail('${escapeHtml(it.doc_id || it.invoice_number)}')">Öffnen &rarr;</button>
              </div>
            `
              )
              .join("");

      return `
        <div class="checklist-box">
          <div style="font-family:var(--font-serif); font-weight:700; font-size:0.95rem; margin-bottom:0.5rem; border-bottom:1px solid var(--hairline); padding-bottom:0.35rem;">
            ${escapeHtml(sec.title)} (${sec.items.length})
          </div>
          ${itemsHtml}
        </div>
      `;
    })
    .join("");

  container.querySelectorAll(".monthend-check-input").forEach((cb) => {
    cb.addEventListener("change", async () => {
      const itemKey = cb.getAttribute("data-item-key");
      const markBooked = cb.getAttribute("data-mark-booked") === "true";
      try {
        await api.toggleMonthEndCheck({
          month: selectedMonth || "all",
          itemKey,
          checked: cb.checked,
          markBooked,
        });
        await loadMonthEndData(selectedMonth || "all");
      } catch (err) {
        showToast(err.message || "Fehler beim Aktualisieren der Checkliste", "error");
      }
    });
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Search, Filters & Delete Modal
// ─────────────────────────────────────────────────────────────────────────────
function setupSearchAndFilters() {
  const searchInput = document.getElementById("ledger-search-input");
  let debounceTimer = null;
  if (searchInput) {
    searchInput.addEventListener("input", (e) => {
      state.searchQuery = e.target.value.trim();
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(async () => {
        try {
          const res = await api.getInvoices(true, state.searchQuery);
          renderLedgerTable(res.invoices || []);
        } catch (err) {
          console.warn("FTS5 search error:", err);
        }
      }, 220);
    });
  }

  document.querySelectorAll(".filter-pill[data-filter]").forEach((pill) => {
    pill.addEventListener("click", () => {
      document.querySelectorAll(".filter-pill[data-filter]").forEach((p) => p.classList.remove("active"));
      pill.classList.add("active");
      state.currentStatusFilter = pill.getAttribute("data-filter");
      renderLedgerTable(state.invoices);
    });
  });
}

function setupDeleteModal() {
  const modal = document.getElementById("delete-modal-overlay");
  const btnCancel = document.getElementById("btn-modal-cancel");
  const btnConfirm = document.getElementById("btn-modal-confirm");

  function closeModal() {
    if (modal) modal.classList.remove("active");
    state.pendingDeleteDocId = null;
  }

  if (btnCancel) btnCancel.addEventListener("click", closeModal);
  if (modal) {
    modal.addEventListener("click", (e) => {
      if (e.target === modal) closeModal();
    });
  }

  if (btnConfirm) {
    btnConfirm.addEventListener("click", async () => {
      if (!state.pendingDeleteDocId) return;
      const docId = state.pendingDeleteDocId;
      closeModal();

      try {
        await api.deleteInvoice(docId, state.conversationId, state.userId);
        showToast("Beleg gelöscht.", "success");
        await refreshData();
      } catch (err) {
        showToast(err.message || "Löschen fehlgeschlagen.", "error");
      }
    });
  }
}

function openDeleteModal(docId, invNum) {
  const modal = document.getElementById("delete-modal-overlay");
  const textEl = document.getElementById("delete-modal-inv-num");
  if (!modal) return;
  state.pendingDeleteDocId = docId;
  if (textEl) textEl.textContent = invNum ? `"${invNum}"` : "Beleg";
  modal.classList.add("active");
}

async function checkHealth() {
  const dot = document.getElementById("health-status-dot");
  const text = document.getElementById("health-status-text");
  try {
    const res = await api.getHealth();
    if (res.status === "ok") {
      if (dot) dot.style.backgroundColor = "var(--stamp-green)";
      if (text) text.textContent = "SQLITE AKTIV";
    } else {
      throw new Error();
    }
  } catch {
    if (dot) dot.style.backgroundColor = "var(--stamp-red)";
    if (text) text.textContent = "OFFLINE";
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Pinned Custom Charts Controller
// ─────────────────────────────────────────────────────────────────────────────
async function loadCustomDashboardCharts() {
  const grid = document.getElementById("dashboard-custom-charts-grid");
  const countBadge = document.getElementById("custom-charts-count-badge");
  if (!grid) return;

  try {
    const res = await api.getDashboardCharts();
    const chartsList = res.charts || [];

    if (countBadge) {
      countBadge.textContent = `${chartsList.length} ANGEHEFTET`;
    }

    if (chartsList.length === 0) {
      grid.innerHTML = `
        <div class="custom-charts-empty-state" id="custom-charts-empty">
          <div style="font-family: var(--font-mono); font-size: 0.76rem; color: var(--ink-muted); margin-bottom: 0.35rem;">
            [ KEINE BENUTZERDEFINIERTEN DIAGRAMME ANGEHEFTET ]
          </div>
          <button class="btn-secondary" onclick="openCustomChartModal()" style="font-size: 0.72rem; padding: 0.35rem 0.75rem; margin-top:0.5rem;">
            + ERSTES DIAGRAMM ANHEFTEN
          </button>
        </div>
      `;
      return;
    }

    grid.innerHTML = chartsList
      .map((item) => {
        const id = item.id;
        const title = escapeHtml(item.title || "Auswertung");
        const prompt = escapeHtml(item.prompt || "");
        const chartPath = item.chart_path;
        return `
          <div class="custom-chart-card" id="card-${id}">
            <div class="custom-chart-header">
              <div>
                <div class="custom-chart-title">${title}</div>
                <div class="custom-chart-prompt">${prompt}</div>
              </div>
              <div class="custom-chart-actions">
                <a href="${chartPath}" target="_blank" class="btn-secondary" style="text-decoration:none; padding:0.25rem 0.55rem; font-size:0.68rem; font-family:var(--font-mono);">
                  VOLLBILD ↗
                </a>
                <button type="button" class="btn-secondary btn-delete-custom-chart" data-id="${id}" style="padding:0.25rem 0.55rem; font-size:0.68rem; color:var(--stamp-red); border-color:var(--hairline); font-family:var(--font-mono);">
                  LÖSCHEN ✕
                </button>
              </div>
            </div>
            <div class="custom-chart-frame-wrapper">
              <iframe src="${chartPath}" loading="lazy"></iframe>
            </div>
          </div>
        `;
      })
      .join("");

    grid.querySelectorAll(".btn-delete-custom-chart").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const chartId = btn.getAttribute("data-id");
        try {
          await api.deleteDashboardChart(chartId);
          await loadCustomDashboardCharts();
        } catch (err) {
          showToast("Fehler beim Entfernen des Diagramms", "error");
        }
      });
    });
  } catch (err) {
    console.warn("Could not load custom dashboard charts:", err);
  }
}

export function openCustomChartModal() {
  const modal = document.getElementById("modal-custom-chart");
  if (modal) modal.style.display = "flex";
}

export function closeCustomChartModal() {
  const modal = document.getElementById("modal-custom-chart");
  if (modal) modal.style.display = "none";
}

window.openCustomChartModal = openCustomChartModal;
window.closeCustomChartModal = closeCustomChartModal;

function setupCustomChartsModal() {
  document.getElementById("btn-close-chart-modal")?.addEventListener("click", closeCustomChartModal);
  document.getElementById("btn-cancel-custom-chart")?.addEventListener("click", closeCustomChartModal);

  const promptInput = document.getElementById("input-custom-chart-prompt");
  const titleInput = document.getElementById("input-custom-chart-title");
  const btnSubmit = document.getElementById("btn-submit-custom-chart");

  document.querySelectorAll(".chart-template-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const p = chip.getAttribute("data-prompt");
      if (promptInput && p) {
        promptInput.value = p;
        if (titleInput) titleInput.value = chip.textContent.trim();
      }
    });
  });

  if (btnSubmit) {
    btnSubmit.addEventListener("click", async () => {
      const prompt = promptInput ? promptInput.value.trim() : "";
      const title = titleInput ? titleInput.value.trim() : "";
      if (!prompt) return;
      btnSubmit.disabled = true;
      try {
        const res = await api.createDashboardChart(prompt, title);
        if (res.status === "success") {
          showToast("Diagramm angeheftet!", "success");
          closeCustomChartModal();
          await loadCustomDashboardCharts();
        }
      } catch (err) {
        showToast(err.message || "Fehler", "error");
      } finally {
        btnSubmit.disabled = false;
      }
    });
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers & Exports
// ─────────────────────────────────────────────────────────────────────────────
export function cleanVendorName(raw) {
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

export function formatMoney(amount, currency = "EUR") {
  return formatMoneyDE(amount, currency);
}

function strLower(s) {
  return String(s || "").trim().toLowerCase();
}

function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
