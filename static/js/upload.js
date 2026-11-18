/**
 * upload.js - Upload Center (Rechnungseingang) with Processing Docket,
 * 9-Rule Validation Badge, and Direct Navigation to Per-Invoice Detail Page & XRechnung Download.
 */

import { api } from "./api.js";
import { formatMoneyDE, formatDateDE } from "./charts.js";

export function initUpload({ onUploadSuccess, getSessionContext, showToast }) {
  const dropzone = document.getElementById("dropzone-card");
  const fileInput = document.getElementById("file-input-upload");
  const processingDocket = document.getElementById("processing-docket");
  const resultCard = document.getElementById("extraction-result-card");
  const btnUploadAnother = document.getElementById("btn-upload-another");
  const btnOpenUploadedDetail = document.getElementById("btn-open-uploaded-detail");

  let lastUploadedDocId = "";

  if (!dropzone || !fileInput) return;

  dropzone.addEventListener("click", () => fileInput.click());

  ["dragenter", "dragover"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.add("dragover");
    });
  });

  ["dragleave", "drop"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.remove("dragover");
    });
  });

  dropzone.addEventListener("drop", (e) => {
    const files = Array.from(e.dataTransfer.files || []);
    if (files.length > 0) {
      handleFiles(files);
    }
  });

  fileInput.addEventListener("change", () => {
    const files = Array.from(fileInput.files || []);
    if (files.length > 0) {
      handleFiles(files);
    }
  });

  if (btnUploadAnother) {
    btnUploadAnother.addEventListener("click", () => {
      if (resultCard) resultCard.style.display = "none";
      if (processingDocket) processingDocket.style.display = "none";
      dropzone.style.display = "block";
      fileInput.value = "";
    });
  }

  if (btnOpenUploadedDetail) {
    btnOpenUploadedDetail.addEventListener("click", () => {
      if (lastUploadedDocId && typeof window.openInvoiceDetail === "function") {
        window.openInvoiceDetail(lastUploadedDocId);
      } else if (typeof window.switchView === "function") {
        window.switchView("ledger");
      }
    });
  }

  async function handleFiles(files) {
    const allowed = [".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tiff"];
    const validFiles = [];

    for (const file of files) {
      const ext = "." + file.name.split(".").pop().toLowerCase();
      if (!allowed.includes(ext)) {
        showToast(`Übersprungen '${file.name}': Dateityp '${ext}' nicht unterstützt.`, "error");
        continue;
      }
      if (file.size > 20 * 1024 * 1024) {
        showToast(`Übersprungen '${file.name}': Datei größer als 20 MB.`, "error");
        continue;
      }
      validFiles.push(file);
    }

    if (validFiles.length === 0) {
      fileInput.value = "";
      return;
    }

    dropzone.style.display = "none";
    if (resultCard) resultCard.style.display = "none";
    if (processingDocket) processingDocket.style.display = "block";

    let lastRes = null;
    let lastFilename = "";

    for (let i = 0; i < validFiles.length; i++) {
      const file = validFiles[i];
      const batchLabel = validFiles.length > 1 ? ` (${i + 1} von ${validFiles.length})` : "";

      const docketFileName = document.getElementById("docket-file-name");
      if (docketFileName) docketFileName.textContent = `${file.name}${batchLabel}`;

      resetDocketStages();

      const startTime = Date.now();
      const timerInterval = setInterval(() => {
        const elapsedSec = ((Date.now() - startTime) / 1000).toFixed(1);
        const timerEl = document.getElementById("docket-timer");
        if (timerEl) timerEl.textContent = `DAUER: ${elapsedSec}s`;
      }, 100);

      setStageStatus("stage-received", "completed", "+0.1s");
      setStageStatus("stage-ocr", "active");
      setStageStatus("stage-extracted", "active");
      setStageStatus("stage-validated", "active");
      setStageStatus("stage-filed", "active");
      setIndeterminateProgress(true);

      const { userId, conversationId } = getSessionContext();

      try {
        showToast(`Verarbeite Beleg ${i + 1}/${validFiles.length}: "${file.name}"...`, "info");
        const res = await api.uploadInvoice(file, conversationId, userId);

        clearInterval(timerInterval);
        setIndeterminateProgress(false);
        setProgress(100);

        const totalElapsed = ((Date.now() - startTime) / 1000).toFixed(2);
        const timerEl = document.getElementById("docket-timer");
        if (timerEl) timerEl.textContent = `ABGESCHLOSSEN IN ${totalElapsed}s`;

        const timings = res.timings || {};
        const tPre = Number(timings.preprocess_s || 0.1);
        const tOcr = Number(timings.ocr_s || 0.3);
        const tStruct = Number(timings.structuring_s || 0.2);
        const tTotal = Number(timings.total_s || totalElapsed);

        setStageStatus("stage-received", "completed", `+${tPre.toFixed(1)}s`);
        setStageStatus("stage-ocr", "completed", `+${(tPre + tOcr).toFixed(1)}s`);
        setStageStatus("stage-extracted", "completed", `+${(tPre + tOcr + tStruct).toFixed(1)}s`);
        setStageStatus("stage-validated", "completed", `+${Math.min(tTotal, tPre + tOcr + tStruct + 0.1).toFixed(1)}s`);
        setStageStatus("stage-filed", "completed", `+${tTotal.toFixed(1)}s`);

        showToast(`Beleg "${file.name}" extrahiert & geprüft (${tTotal.toFixed(1)}s)`, "success");
        lastRes = res;
        lastFilename = file.name;

        if (i < validFiles.length - 1) {
          await new Promise((r) => setTimeout(r, 400));
        }
      } catch (err) {
        clearInterval(timerInterval);
        setIndeterminateProgress(false);
        showDocketError(err.message || `Fehler bei ${file.name}`);
        showToast(`Fehler bei "${file.name}": ${err.message}`, "error");
        if (i < validFiles.length - 1) {
          await new Promise((r) => setTimeout(r, 900));
        }
      }
    }

    fileInput.value = "";

    if (typeof onUploadSuccess === "function") {
      await onUploadSuccess();
    }

    if (lastRes) {
      setTimeout(() => {
        renderExtractionResult(lastRes, lastFilename);
      }, 450);
    }
  }

  function resetDocketStages() {
    setIndeterminateProgress(false);
    ["received", "ocr", "extracted", "validated", "filed"].forEach((stage) => {
      setStageStatus(`stage-${stage}`, "pending", "--:--");
    });
    setProgress(0);
    const errBox = document.getElementById("docket-error-box");
    if (errBox) errBox.style.display = "none";
  }

  function setStageStatus(elementId, status, timeText = "") {
    const row = document.getElementById(elementId);
    if (!row) return;

    const stampEl = row.querySelector(".docket-stage-stamp");
    const timeEl = row.querySelector(".docket-stage-time");

    if (stampEl) {
      if (status === "completed") {
        stampEl.className = "docket-stage-stamp stamp-badge paid";
        stampEl.textContent = "OK \u2713";
      } else if (status === "active") {
        stampEl.className = "docket-stage-stamp stamp-badge pending is-loading";
        stampEl.innerHTML = `<span class="docket-inline-spinner"></span> AKTIV`;
      } else if (status === "failed") {
        stampEl.className = "docket-stage-stamp stamp-badge failed";
        stampEl.textContent = "FEHLER";
      } else {
        stampEl.className = "docket-stage-stamp stamp-badge";
        stampEl.style.color = "var(--ink-muted)";
        stampEl.style.borderColor = "var(--hairline)";
        stampEl.textContent = "WARTEND";
      }
    }

    if (timeEl) {
      if (timeText) {
        timeEl.textContent = timeText;
      } else if (status === "active") {
        timeEl.textContent = "PRÜFUNG...";
      } else {
        timeEl.textContent = "--:--";
      }
    }
  }

  function setProgress(pct) {
    const fill = document.getElementById("docket-progress-fill");
    if (fill) fill.style.width = `${pct}%`;
  }

  function setIndeterminateProgress(enable) {
    const fill = document.getElementById("docket-progress-fill");
    if (!fill) return;
    fill.classList.toggle("is-indeterminate", Boolean(enable));
  }

  function showDocketError(reason) {
    setStageStatus("stage-ocr", "failed");
    setStageStatus("stage-extracted", "failed");
    setStageStatus("stage-validated", "failed");
    setStageStatus("stage-filed", "failed");

    const errBox = document.getElementById("docket-error-box");
    const errMsg = document.getElementById("docket-error-msg");
    if (errBox && errMsg) {
      errMsg.textContent = reason;
      errBox.style.display = "block";
    }
  }

  function renderExtractionResult(res, filename) {
    if (!resultCard) return;

    const summary = res.invoice_summary || {};
    lastUploadedDocId = res.doc_id || summary.doc_id || summary.invoice_number || "";

    const setText = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = val || "N/A";
    };

    setText("res-filename", filename);
    setText("res-inv-num", summary.invoice_number);
    setText("res-vendor", summary.vendor_name || "Unbekannt");
    setText("res-date", formatDateDE(summary.invoice_date));
    setText("res-amount", formatMoneyDE(summary.total_amount || 0, summary.currency || "EUR"));
    setText("res-status", summary.payment_status === "Paid" ? "BEZAHLT" : "OFFEN");

    const valEl = document.getElementById("res-validation-status");
    if (valEl) {
      const vr = summary.validation_report || {};
      const st = summary.validation_status || vr.overall_status || "PASS";
      valEl.textContent = `${st} (${vr.passed_count || 0}/9 OK)`;
      valEl.style.color = st === "FAIL" ? "var(--stamp-red)" : (st === "WARN" ? "var(--stamp-amber)" : "var(--stamp-green)");
    }

    if (processingDocket) processingDocket.style.display = "none";
    resultCard.style.display = "block";
  }
}
