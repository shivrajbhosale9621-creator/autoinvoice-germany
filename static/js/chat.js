/**
 * chat.js - Conversational AI Copilot on Paper (SQL-Only + Citations + Human Approval Cards)
 */

import { api } from "./api.js";

export function initChat({ getSessionContext, setConversationId, showToast, onActionResolved, t: translate }) {
  const t = typeof translate === "function" ? translate : (k) => k;
  const messagesContainer = document.getElementById("chat-messages");
  const textarea = document.getElementById("chat-textarea");
  const btnSend = document.getElementById("btn-chat-send");
  const btnAttach = document.getElementById("btn-chat-attach");
  const fileInput = document.getElementById("file-input-chat");
  const attachmentPreview = document.getElementById("chat-attachment-preview");
  const attachmentName = document.getElementById("chat-attachment-name");
  const btnRemoveAttach = document.getElementById("btn-remove-attachment");
  const btnNewChat = document.getElementById("btn-new-chat");
  const convListEl = document.getElementById("chat-conversations-list");
  const promptChips = document.querySelectorAll(".quick-prompt-chip");

  let attachedFile = null;
  let isSending = false;

  if (btnNewChat) {
    btnNewChat.addEventListener("click", () => {
      const newId = "conv_" + Math.random().toString(36).substring(2, 10);
      setConversationId(newId);
      clearMessages();
      appendAssistantMessage(t("chat_welcome_memo"));
      highlightActiveConversation(newId);
      showToast(t("chat_new_conv_toast"), "info");
    });
  }

  promptChips.forEach((chip) => {
    chip.addEventListener("click", () => {
      if (isSending) {
        showToast(t("chat_busy_toast"), "info");
        return;
      }
      const prompt = chip.getAttribute("data-prompt") || chip.textContent.trim();
      if (typeof window.switchView === "function") {
        window.switchView("chat");
      }
      if (textarea) {
        textarea.value = prompt;
        sendMessage();
      }
    });
  });

  if (btnAttach && fileInput) {
    btnAttach.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => {
      if (fileInput.files && fileInput.files.length > 0) {
        setAttachedFile(fileInput.files[0]);
      }
    });
  }

  if (btnRemoveAttach) {
    btnRemoveAttach.addEventListener("click", () => clearAttachedFile());
  }

  if (textarea) {
    textarea.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });

    textarea.addEventListener("input", () => {
      textarea.style.height = "auto";
      textarea.style.height = Math.min(textarea.scrollHeight, 120) + "px";
    });
  }

  if (btnSend) {
    btnSend.addEventListener("click", () => sendMessage());
  }

  function setAttachedFile(file) {
    attachedFile = file;
    if (attachmentPreview && attachmentName) {
      attachmentName.textContent = `${t("chat_attachment_prefix")}: ${file.name} [${(file.size / 1024).toFixed(1)} KB]`;
      attachmentPreview.style.display = "flex";
    }
  }

  function clearAttachedFile() {
    attachedFile = null;
    if (fileInput) fileInput.value = "";
    if (attachmentPreview) attachmentPreview.style.display = "none";
  }

  async function sendMessage() {
    if (isSending) return;
    const text = textarea ? textarea.value.trim() : "";
    if (!text && !attachedFile) return;

    isSending = true;
    if (btnSend) btnSend.disabled = true;
    promptChips.forEach((c) => (c.disabled = true));

    const { userId, conversationId, lang } = getSessionContext();
    const currentFile = attachedFile;

    let userDisplay = text;
    if (currentFile) {
      userDisplay += `\n\n*[${t("chat_attached_doc_label")}: ${currentFile.name}]*`;
    }
    appendUserMessage(userDisplay);

    if (textarea) {
      textarea.value = "";
      textarea.style.height = "auto";
    }
    clearAttachedFile();

    const thinkingId = appendAssistantMessage(t("chat_thinking_text"), true);

    try {
      const res = await api.chat({
        message: text || t("chat_default_file_msg"),
        userId,
        conversationId,
        file: currentFile,
        lang: lang || "de",
      });

      removeMessage(thinkingId);
      appendAssistantMessage(res.response || t("chat_no_answer"));

      if (res.conversation_id && res.conversation_id !== conversationId) {
        setConversationId(res.conversation_id);
      }

      loadConversations();
      if (currentFile && typeof onActionResolved === "function") {
        onActionResolved();
      }
    } catch (err) {
      removeMessage(thinkingId);
      const msg = err.message || t("chat_transient_err");
      appendAssistantMessage(`**${t("chat_system_notice_hdr")}**\n\n${msg}`);
      showToast(msg, "error");
    } finally {
      isSending = false;
      if (btnSend) btnSend.disabled = false;
      promptChips.forEach((c) => (c.disabled = false));
    }
  }

  async function loadConversations() {
    if (!convListEl) return;
    const { userId, conversationId } = getSessionContext();

    try {
      const res = await api.getConversations(userId);
      const convs = res.conversations || [];

      convListEl.innerHTML = "";
      if (convs.length === 0) {
        convListEl.innerHTML = `
          <li style="padding: 1.5rem 1rem; text-align: center; color: var(--ink-muted); font-family: var(--font-mono); font-size: 0.72rem;">
            [ ${t("chat_no_archived_sessions")} ]
          </li>`;
        return;
      }

      convs.forEach((c) => {
        const li = document.createElement("li");
        li.className = `chat-conv-item ${c.conversation_id === conversationId ? "active" : ""}`;
        li.setAttribute("data-id", c.conversation_id);

        const dateStr = c.updated_at ? new Date(c.updated_at).toLocaleDateString("de-DE") : "";
        li.innerHTML = `
          <div class="conv-item-title">${escapeHtml(c.title || t("chat_conv_default_title"))}</div>
          <div class="conv-item-date">${dateStr} &bull; ${escapeHtml(c.conversation_id.slice(0, 8))}</div>
        `;

        li.addEventListener("click", () => switchConversation(c.conversation_id));
        convListEl.appendChild(li);
      });
    } catch (err) {
      console.warn("Could not load conversations:", err);
    }
  }

  async function switchConversation(convId) {
    const { userId } = getSessionContext();
    setConversationId(convId);
    highlightActiveConversation(convId);

    clearMessages();
    const loadingId = appendAssistantMessage(t("chat_loading_history"), true);

    try {
      const res = await api.getChatHistory(convId, userId, 50);
      removeMessage(loadingId);

      const msgs = res.messages || [];
      if (msgs.length === 0) {
        appendAssistantMessage(t("chat_empty_session"));
        return;
      }

      msgs.forEach((m) => {
        if (m.role === "user") {
          appendUserMessage(m.content);
        } else if (m.role === "assistant") {
          appendAssistantMessage(m.content);
        }
      });
    } catch (err) {
      removeMessage(loadingId);
      appendAssistantMessage(`${t("chat_load_err")}: ${err.message}`);
    }
  }

  function highlightActiveConversation(convId) {
    if (!convListEl) return;
    convListEl.querySelectorAll(".chat-conv-item").forEach((it) => {
      it.classList.toggle("active", it.getAttribute("data-id") === convId);
    });
  }

  function clearMessages() {
    if (messagesContainer) messagesContainer.innerHTML = "";
  }

  function appendUserMessage(text) {
    if (!messagesContainer) return;
    const div = document.createElement("div");
    div.className = "chat-msg user";
    div.innerHTML = `
      <div class="chat-avatar" title="${escapeHtml(t("chat_user_title"))}">${escapeHtml(t("chat_user_label"))}</div>
      <div class="msg-body">${formatMarkdown(text)}</div>
    `;
    messagesContainer.appendChild(div);
    scrollToBottom();
  }

  function appendAssistantMessage(text, isTemporary = false) {
    if (!messagesContainer) return null;
    const id = "msg_" + Math.random().toString(36).substring(2, 9);
    const div = document.createElement("div");
    div.id = id;
    div.className = `chat-msg assistant ${isTemporary ? "is-thinking" : ""}`;

    if (isTemporary) {
      div.innerHTML = `
        <div class="chat-avatar" title="${escapeHtml(t("chat_ai_title"))}">${escapeHtml(t("chat_ai_label"))}</div>
        <div class="msg-body">
          <div class="chat-thinking-container">
            <span class="thinking-pulse-dot"></span>
            <span class="thinking-stage-text" id="${id}-stage-text">${escapeHtml(text)}</span>
          </div>
        </div>
      `;
      messagesContainer.appendChild(div);
      scrollToBottom();
      return id;
    }

    div.innerHTML = `
      <div class="chat-avatar" title="${escapeHtml(t("chat_ai_title"))}">${escapeHtml(t("chat_ai_label"))}</div>
      <div class="msg-body">${formatMarkdown(text)}</div>
    `;

    // Bind clickable invoice citation links [RE-2026-10418](#invoice/RE-2026-10418)
    div.querySelectorAll("a[href^='#invoice/']").forEach((a) => {
      a.classList.add("chat-citation-pill");
      a.addEventListener("click", (e) => {
        e.preventDefault();
        const invIdentifier = decodeURIComponent(a.getAttribute("href").replace("#invoice/", ""));
        if (typeof window.openInvoiceDetail === "function") {
          window.openInvoiceDetail(invIdentifier);
        }
      });
    });

    // Detect Excel report filename
    const reportMatch = text.match(/([a-zA-Z0-9_\-]+\.xlsx)/);
    if (reportMatch && reportMatch[1]) {
      const filename = reportMatch[1];
      const card = createReportDownloadCard(filename);
      div.querySelector(".msg-body").appendChild(card);
    }

    // Detect Chart HTML path
    const chartRegex = /(?:\[CHART:\s*([^\]]+)\]|\[(?:[^\]]*)\]\((\/static\/charts\/[^\)]+)\))/g;
    let chartMatch;
    const detectedUrls = new Set();
    while ((chartMatch = chartRegex.exec(text)) !== null) {
      const chartUrl = (chartMatch[1] || chartMatch[2] || "").trim();
      if (chartUrl && !detectedUrls.has(chartUrl)) {
        detectedUrls.add(chartUrl);
        const card = createChartCard(chartUrl);
        div.querySelector(".msg-body").appendChild(card);
      }
    }

    // Tier 2: Detect Copilot Action Proposal [ACTION_PROPOSAL: act_xxx]
    const actionMatch = text.match(/\[ACTION_PROPOSAL:\s*([a-zA-Z0-9_]+)\]/);
    if (actionMatch && actionMatch[1]) {
      const actionId = actionMatch[1].trim();
      const approvalCard = createApprovalCard(actionId);
      div.querySelector(".msg-body").appendChild(approvalCard);
    }

    messagesContainer.appendChild(div);
    scrollToBottom();
    return id;
  }

  function createApprovalCard(actionId) {
    const card = document.createElement("div");
    card.className = "report-download-card";
    card.style.borderColor = "var(--stamp-amber)";
    card.style.background = "#FBF4E6";
    card.innerHTML = `
      <div class="report-file-meta">
        <span class="stamp-badge pending">${escapeHtml(t("chat_approval_required"))}</span>
        <div class="report-file-info">
          <strong>${escapeHtml(t("chat_human_confirm_title"))}</strong>
          <span>${escapeHtml(t("chat_action_id_label"))}: ${escapeHtml(actionId)}</span>
        </div>
      </div>
      <div style="display:flex; gap:0.5rem;" id="approval-btns-${escapeHtml(actionId)}">
        <button type="button" class="btn-secondary btn-reject-action" style="font-size:0.74rem; padding:0.35rem 0.7rem;">
          ${escapeHtml(t("chat_btn_reject"))}
        </button>
        <button type="button" class="btn-primary btn-approve-action" style="font-size:0.74rem; padding:0.35rem 0.8rem; background:var(--stamp-green); border-color:var(--stamp-green);">
          ${escapeHtml(t("chat_btn_approve"))}
        </button>
      </div>
    `;

    const btnApprove = card.querySelector(".btn-approve-action");
    const btnReject = card.querySelector(".btn-reject-action");
    const btnWrap = card.querySelector(`#approval-btns-${actionId}`);

    async function handleDecision(decision) {
      if (btnApprove) btnApprove.disabled = true;
      if (btnReject) btnReject.disabled = true;
      try {
        const res = await api.resolveCopilotAction(actionId, decision);
        if (btnWrap) {
          const isApproved = res.action_status === "approved";
          btnWrap.innerHTML = `<span class="stamp-badge ${isApproved ? "paid" : "failed"}">${escapeHtml(isApproved ? t("chat_status_approved") : t("chat_status_rejected"))}</span>`;
        }
        showToast(res.message || t("chat_action_updated"), "success");
        if (typeof onActionResolved === "function") {
          onActionResolved();
        }
      } catch (err) {
        showToast(err.message || t("chat_approval_err"), "error");
        if (btnApprove) btnApprove.disabled = false;
        if (btnReject) btnReject.disabled = false;
      }
    }

    if (btnApprove) btnApprove.addEventListener("click", () => handleDecision("approve"));
    if (btnReject) btnReject.addEventListener("click", () => handleDecision("reject"));
    return card;
  }

  function removeMessage(id) {
    if (!id) return;
    const el = document.getElementById(id);
    if (el) el.remove();
  }

  function scrollToBottom() {
    if (messagesContainer) {
      messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
  }

  function createReportDownloadCard(filename) {
    const card = document.createElement("div");
    card.className = "report-download-card";
    const downloadUrl = api.getReportDownloadUrl(filename);

    card.innerHTML = `
      <div class="report-file-meta">
        <div class="report-file-icon">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.75">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
            <polyline points="14 2 14 8 20 8"></polyline>
          </svg>
        </div>
        <div class="report-file-info">
          <strong>${escapeHtml(t("chat_excel_created"))}</strong>
          <span>${escapeHtml(filename)}</span>
        </div>
      </div>
      <a href="${downloadUrl}" download="${filename}" class="btn-primary" style="text-decoration:none; padding: 0.35rem 0.75rem; font-size: 0.75rem;">
        ${escapeHtml(t("chat_btn_download"))}
      </a>
    `;
    return card;
  }

  function createChartCard(chartUrl) {
    const card = document.createElement("div");
    card.className = "report-download-card";
    card.style.flexDirection = "column";
    card.style.alignItems = "stretch";
    card.style.gap = "0.6rem";
    card.style.marginTop = "0.75rem";
    card.style.padding = "0.75rem";
    card.style.background = "var(--paper-card)";
    card.style.border = "1px solid var(--hairline)";

    card.innerHTML = `
      <div style="display:flex; justify-content:space-between; align-items:center; border-bottom: 1px dashed var(--hairline); padding-bottom: 0.4rem;">
        <strong style="font-family: var(--font-mono); font-size: 0.72rem; letter-spacing: 0.04em;">${escapeHtml(t("chat_chart_hdr"))}</strong>
        <a href="${chartUrl}" target="_blank" class="btn-secondary" style="text-decoration:none; padding: 0.2rem 0.55rem; font-size: 0.68rem; font-family: var(--font-mono);">
          ${escapeHtml(t("chat_btn_fullscreen"))}
        </a>
      </div>
      <div style="width: 100%; height: 340px; background: #FDFBF5; border: 1px solid var(--hairline); border-radius: 2px; overflow: hidden;">
        <iframe src="${chartUrl}" style="width: 100%; height: 100%; border: none;" loading="lazy"></iframe>
      </div>
    `;
    return card;
  }

  function formatMarkdown(text) {
    let cleanText = (text || "")
      .replace(/-/g, "-")
      .replace(/\[CHART:\s*[^\]]+\]/g, "")
      .replace(/\[ACTION_PROPOSAL:\s*[^\]]+\]/g, "")
      .replace(/\[(?:[^\]]*)\]\(\/static\/charts\/[^\)]+\)/g, "")
      .trim();
    if (typeof marked !== "undefined" && typeof marked.parse === "function") {
      return marked.parse(cleanText);
    }
    let html = escapeHtml(cleanText)
      .replace(/\[([^\]]+)\]\((#invoice\/[^\)]+)\)/g, '<a href="$2" class="chat-citation-pill">$1</a>')
      .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.*?)\*/g, "<em>$1</em>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\n\n/g, "</p><p>")
      .replace(/\n/g, "<br>");
    return `<p>${html}</p>`;
  }

  function escapeHtml(str) {
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  return {
    loadConversations,
    switchConversation,
  };
}
