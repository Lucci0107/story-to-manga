/* Story to Mangaの画面操作。APIレスポンスは常にJSONとして扱う。 */
(function () {
  "use strict";

  const root = document.documentElement;
  const toastRegion = document.getElementById("toast-region");

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[char];
    });
  }

  function escapeAttr(value) {
    return escapeHtml(value).replace(/`/g, "&#096;");
  }

  function parseJson(value, fallback) {
    try { return JSON.parse(value); } catch (_error) { return fallback; }
  }

  function showToast(message, type) {
    if (!toastRegion) return;
    const toast = document.createElement("div");
    toast.className = "toast" + (type === "error" ? " error" : "");
    toast.textContent = message;
    toastRegion.appendChild(toast);
    window.setTimeout(function () { toast.remove(); }, 4200);
  }

  const processingDialogElements = {
    root: document.getElementById("processing-dialog-root"),
    dialog: document.getElementById("processing-dialog"),
    title: document.getElementById("processing-dialog-title"),
    message: document.getElementById("processing-dialog-message"),
    progress: document.getElementById("processing-dialog-progress"),
    submessage: document.getElementById("processing-dialog-submessage"),
    action: document.getElementById("processing-dialog-action")
  };
  let processingDialogOpen = false;
  let processingDialogPreviousFocus = null;
  let processingDialogAction = null;

  function updateProcessingDialog(options) {
    const values = options || {};
    if (!processingDialogElements.root) return;
    if (values.title != null && processingDialogElements.title) processingDialogElements.title.textContent = values.title;
    if (values.message != null && processingDialogElements.message) processingDialogElements.message.textContent = values.message;
    if (Object.prototype.hasOwnProperty.call(values, "progress") && processingDialogElements.progress) {
      const progress = String(values.progress == null ? "" : values.progress).trim();
      processingDialogElements.progress.textContent = progress;
      processingDialogElements.progress.hidden = !progress;
    }
    if (values.submessage != null && processingDialogElements.submessage) processingDialogElements.submessage.textContent = values.submessage;
    if (Object.prototype.hasOwnProperty.call(values, "actionLabel") && processingDialogElements.action) {
      const label = String(values.actionLabel || "").trim();
      processingDialogElements.action.textContent = label;
      processingDialogElements.action.hidden = !label;
    }
    if (Object.prototype.hasOwnProperty.call(values, "action")) {
      processingDialogAction = typeof values.action === "function" ? values.action : null;
    }
  }

  function showProcessingDialog(options) {
    if (!processingDialogElements.root) return;
    if (!processingDialogOpen) {
      processingDialogPreviousFocus = document.activeElement;
      processingDialogOpen = true;
      processingDialogElements.root.hidden = false;
      processingDialogElements.root.setAttribute("aria-hidden", "false");
      processingDialogElements.root.setAttribute("aria-busy", "true");
      document.body.classList.add("processing-dialog-open");
    }
    updateProcessingDialog({
      title: "処理中です",
      message: "処理を開始しています…",
      progress: "",
      submessage: "完了するまでお待ちください",
      actionLabel: "",
      action: null,
      ...(options || {})
    });
    const focusDialog = function () {
      if (processingDialogOpen) processingDialogElements.dialog?.focus();
    };
    if (window.requestAnimationFrame) window.requestAnimationFrame(focusDialog); else focusDialog();
  }

  function hideProcessingDialog() {
    if (!processingDialogElements.root || !processingDialogOpen) return;
    processingDialogOpen = false;
    processingDialogAction = null;
    if (processingDialogElements.action) processingDialogElements.action.hidden = true;
    processingDialogElements.root.hidden = true;
    processingDialogElements.root.setAttribute("aria-hidden", "true");
    processingDialogElements.root.setAttribute("aria-busy", "false");
    document.body.classList.remove("processing-dialog-open");
    const previous = processingDialogPreviousFocus;
    processingDialogPreviousFocus = null;
    if (previous && previous.isConnected && typeof previous.focus === "function") previous.focus();
  }

  document.addEventListener("keydown", function (event) {
    if (!processingDialogOpen) return;
    if (event.key === "Escape" || event.key === "Tab") {
      event.preventDefault();
      if (event.key === "Tab" && processingDialogElements.action && !processingDialogElements.action.hidden && document.activeElement !== processingDialogElements.action) {
        processingDialogElements.action.focus();
      } else {
        processingDialogElements.dialog?.focus();
      }
    }
  });
  processingDialogElements.action?.addEventListener("click", function () {
    if (processingDialogAction) processingDialogAction();
  });

  function applyTheme(theme) {
    root.dataset.theme = theme;
    window.localStorage.setItem("story-manga-theme", theme);
    document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
      button.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
    });
  }

  const savedTheme = window.localStorage.getItem("story-manga-theme");
  applyTheme(savedTheme === "dark" ? "dark" : "light");
  document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
    button.addEventListener("click", function () {
      applyTheme(root.dataset.theme === "dark" ? "light" : "dark");
    });
  });

  const registerToggle = document.querySelector("[data-auth-register]");
  if (registerToggle) {
    registerToggle.addEventListener("click", function () {
      const form = document.querySelector(".register-form");
      if (!form) return;
      form.hidden = !form.hidden;
      registerToggle.textContent = form.hidden ? "新規登録" : "ログインに戻る";
      if (!form.hidden) form.querySelector("input")?.focus();
    });
  }

  const newProjectForm = document.getElementById("new-project-form");
  if (newProjectForm) initNewProjectForm(newProjectForm);
  initKnowledgeScreens();
  initAIModelSettings();

  document.querySelectorAll("[data-delete-project]").forEach(function (button) {
    button.addEventListener("click", async function () {
      if (!window.confirm("このProjectを削除しますか？保存した本文と制作データも削除されます。")) return;
      button.disabled = true;
      try {
        const response = await fetch("/api/projects/" + encodeURIComponent(button.dataset.deleteProject), { method: "DELETE", headers: { Accept: "application/json" } });
        const data = await response.json().catch(function () { return {}; });
        if (!response.ok) throw new Error(data.detail || "Projectを削除できませんでした");
        window.location.reload();
      } catch (error) {
        button.disabled = false;
        showToast(error.message, "error");
      }
    });
  });

  const projectDataNode = document.getElementById("project-data");
  if (projectDataNode) {
    const initialProject = parseJson(projectDataNode.textContent || "{}", null);
    if (initialProject) initWorkspace(initialProject);
  }

  function initNewProjectForm(form) {
    const count = document.getElementById("story-count");
    const story = document.getElementById("story-text");
    const file = document.getElementById("story-file");
    const selectedFile = document.getElementById("selected-file");
    const errorBox = document.getElementById("new-project-error");
    const tabs = form.querySelectorAll("[data-import-mode]");
    const panels = form.querySelectorAll("[data-import-panel]");

    function updateCount() {
      if (count && story) count.textContent = story.value.length.toLocaleString("ja-JP") + "文字";
    }
    story?.addEventListener("input", updateCount);
    file?.addEventListener("change", function () {
      selectedFile.textContent = file.files?.[0] ? file.files[0].name + " を選択中" : "";
    });
    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        const mode = tab.dataset.importMode;
        tabs.forEach(function (item) {
          const active = item === tab;
          item.classList.toggle("active", active);
          item.setAttribute("aria-selected", active ? "true" : "false");
        });
        panels.forEach(function (panel) {
          panel.hidden = panel.dataset.importPanel !== mode;
          panel.classList.toggle("active", panel.dataset.importPanel === mode);
        });
      });
    });
    const drop = form.querySelector(".file-drop");
    ["dragenter", "dragover"].forEach(function (eventName) {
      drop?.addEventListener(eventName, function (event) { event.preventDefault(); drop.classList.add("dragging"); });
    });
    ["dragleave", "drop"].forEach(function (eventName) {
      drop?.addEventListener(eventName, function (event) { event.preventDefault(); drop.classList.remove("dragging"); });
    });
    drop?.addEventListener("drop", function (event) {
      if (!file || !event.dataTransfer.files.length) return;
      file.files = event.dataTransfer.files;
      file.dispatchEvent(new Event("change"));
    });
    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      errorBox.hidden = true;
      const submit = form.querySelector("button[type=submit]");
      if (submit.disabled) return;
      submit.disabled = true;
      submit.dataset.originalText = submit.textContent;
      submit.textContent = "本文を保存中…";
      showProcessingDialog({
        message: "物語を取り込んでいます…",
        progress: "本文を抽出しています",
        submessage: "入力内容をProjectへ保存しています。"
      });
      try {
        const response = await fetch(form.action, { method: "POST", body: new FormData(form), headers: { Accept: "application/json" } });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Projectを作成できませんでした");
        window.location.href = "/projects/" + encodeURIComponent(data.project.id);
      } catch (error) {
        errorBox.textContent = error.message || "入力を確認してください";
        errorBox.hidden = false;
        showToast(errorBox.textContent, "error");
      } finally {
        hideProcessingDialog();
        submit.disabled = false;
        submit.textContent = submit.dataset.originalText;
      }
    });
  }

  function initKnowledgeScreens() {
    const uploadForm = document.getElementById("knowledge-upload-form");
    const versionForm = document.getElementById("knowledge-version-form");
    const metadataForm = document.getElementById("knowledge-metadata-form");
    const jsonApi = async function (url, options) {
      const response = await fetch(url, { headers: { Accept: "application/json", ...(options?.headers || {}) }, ...options });
      const data = await response.json().catch(function () { return {}; });
      if (!response.ok) throw new Error(data.detail || "Knowledgeを更新できませんでした");
      return data;
    };
    const bindFileName = function (inputId, targetId) {
      const input = document.getElementById(inputId);
      const target = document.getElementById(targetId);
      input?.addEventListener("change", function () { if (target) target.textContent = input.files?.[0]?.name || ""; });
    };
    bindFileName("knowledge-file", "knowledge-selected-file");
    bindFileName("knowledge-version-file", "knowledge-version-selected-file");

    [uploadForm, versionForm].forEach(function (form) {
      form?.addEventListener("submit", async function (event) {
        event.preventDefault();
        const submit = form.querySelector("button[type=submit]");
        const errorBox = form.querySelector(".inline-form-error");
        if (!submit || submit.disabled) return;
        submit.disabled = true;
        const original = submit.textContent;
        submit.textContent = "抽出・索引化中…";
        if (errorBox) errorBox.hidden = true;
        showProcessingDialog({
          message: "ナレッジを処理しています…",
          progress: "本文を抽出して索引化しています",
          submessage: "内容を分割し、あとから参照できる形で保存しています。"
        });
        try {
          const data = await jsonApi(form.action, { method: "POST", body: new FormData(form) });
          showToast(data.duplicate ? "同じ内容のVersionがあるため、追加しませんでした" : "KnowledgeをReadyにしました");
          window.location.reload();
        } catch (error) {
          if (errorBox) { errorBox.textContent = error.message; errorBox.hidden = false; }
          showToast(error.message, "error");
          submit.disabled = false;
          submit.textContent = original;
        } finally {
          hideProcessingDialog();
        }
      });
    });

    metadataForm?.addEventListener("submit", async function (event) {
      event.preventDefault();
      const data = new FormData(metadataForm);
      const errorBox = document.getElementById("knowledge-metadata-error");
      const submit = metadataForm.querySelector("button[type=submit]");
      if (!submit || submit.disabled) return;
      submit.disabled = true;
      try {
        await jsonApi("/api/knowledge/" + encodeURIComponent(metadataForm.dataset.knowledgeId), { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: data.get("title"), description: data.get("description"), category: data.get("category") }) });
        showToast("Knowledge設定を保存しました");
        window.location.reload();
      } catch (error) {
        if (errorBox) { errorBox.textContent = error.message; errorBox.hidden = false; }
        showToast(error.message, "error");
        submit.disabled = false;
      }
    });

    document.querySelectorAll("[data-toggle-knowledge]").forEach(function (button) {
      button.addEventListener("click", async function () {
        const active = button.dataset.active === "true";
        button.disabled = true;
        try {
          await jsonApi("/api/knowledge/" + encodeURIComponent(button.dataset.knowledgeId), { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ active: !active }) });
          showToast(active ? "Knowledgeを無効にしました" : "Knowledgeを有効にしました");
          window.location.reload();
        } catch (error) { button.disabled = false; showToast(error.message, "error"); }
      });
    });

    document.querySelectorAll("[data-archive-knowledge]").forEach(function (button) {
      button.addEventListener("click", async function () {
        const archived = button.dataset.archived === "true";
        const message = archived ? "このKnowledgeをLibraryへ戻しますか？" : "このKnowledgeをアーカイブしますか？Projectの自動参照から外れます。";
        if (!window.confirm(message)) return;
        button.disabled = true;
        try {
          await jsonApi("/api/knowledge/" + encodeURIComponent(button.dataset.knowledgeId), { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ archived: !archived }) });
          showToast(archived ? "KnowledgeをLibraryへ戻しました" : "Knowledgeをアーカイブしました");
          window.location.reload();
        } catch (error) { button.disabled = false; showToast(error.message, "error"); }
      });
    });

    document.querySelectorAll("[data-activate-version]").forEach(function (button) {
      button.addEventListener("click", async function () {
        if (!window.confirm("このVersionを現在有効なVersionにしますか？")) return;
        button.disabled = true;
        try {
          await jsonApi("/api/knowledge/" + encodeURIComponent(button.dataset.knowledgeId) + "/versions/" + encodeURIComponent(button.dataset.versionId) + "/activate", { method: "POST" });
          showToast("Versionを有効化しました");
          window.location.reload();
        } catch (error) { button.disabled = false; showToast(error.message, "error"); }
      });
    });

    document.querySelectorAll("[data-delete-knowledge]").forEach(function (button) {
      button.addEventListener("click", async function () {
        if (!window.confirm("このKnowledgeと全Versionを削除しますか？この操作は保存されます。")) return;
        button.disabled = true;
        try {
          await jsonApi("/api/knowledge/" + encodeURIComponent(button.dataset.deleteKnowledge), { method: "DELETE" });
          window.location.href = "/knowledge";
        } catch (error) { button.disabled = false; showToast(error.message, "error"); }
      });
    });
  }

  function initAIModelSettings() {
    const rootNode = document.getElementById("ai-model-settings-root");
    const dataNode = document.getElementById("ai-model-settings-data");
    if (!rootNode || !dataNode) return;
    let state = parseJson(dataNode.textContent || "{}", {});
    const taskOrder = ["story_analysis", "adaptation", "settings_recommendation", "character", "storyboard", "qa", "panel_prompt"];
    const taskKeys = taskOrder.map(function (task) { return task + "_model"; });
    const defaultValues = {
      preset: "auto",
      story_analysis_model: "auto",
      adaptation_model: "auto",
      settings_recommendation_model: "auto",
      character_model: "auto",
      storyboard_model: "auto",
      qa_model: "auto",
      panel_prompt_model: "auto",
      image_model: "gpt-image-2",
      reasoning_effort: "auto"
    };

    function values() {
      return { ...defaultValues, ...(state.settings?.global || {}) };
    }

    function statusInfo(modelId) {
      const item = state.availability?.[modelId] || { status: "not_checked" };
      const labels = { available: "利用可能", unavailable: "このアカウントでは未利用", not_checked: "未確認", temporarily_unavailable: "一時的に確認できません" };
      return { status: item.status || "not_checked", label: labels[item.status] || labels.not_checked };
    }

    function modelLabel(modelId, fallback) {
      const models = [...(state.registry?.text_models || []), ...(state.registry?.image_models || [])];
      const model = models.find(function (item) { return item.id === modelId; });
      return model?.display_name || fallback || modelId;
    }

    function render() {
      const current = values();
      const registry = state.registry || { text_models: [], image_models: [], tasks: [], presets: [], reasoning_levels: [] };
      const textModels = registry.text_models || [];
      const imageModels = registry.image_models || [];
      const presetOptions = (registry.presets || []).map(function (item) {
        return '<option value="' + escapeAttr(item.id) + '"' + (item.id === current.preset ? " selected" : "") + '>' + escapeHtml(item.label) + '</option>';
      }).join("");
      const modelSelect = function (key, label, help) {
        const options = '<option value="auto"' + (current[key] === "auto" ? " selected" : "") + '>Auto（プリセット）</option>' + textModels.map(function (item) {
          return '<option value="' + escapeAttr(item.id) + '"' + (item.id === current[key] ? " selected" : "") + '>' + escapeHtml(item.display_name) + '</option>';
        }).join("");
        const selected = current[key] === "auto" ? "Auto（プリセット）" : modelLabel(current[key], current[key]);
        const availability = current[key] === "gpt-6-astra" || (current[key] === "auto" && ["highest_quality"].includes(current.preset)) ? statusInfo("gpt-6-astra") : null;
        return '<label class="ai-model-field"><span>' + escapeHtml(label) + '<small>' + escapeHtml(help || "") + '</small></span><select aria-label="' + escapeAttr(label) + 'のモデル" data-ai-model-field="' + key + '">' + options + '</select>' + (availability ? '<em class="availability-state availability-' + escapeAttr(availability.status) + '">Astra: ' + escapeHtml(availability.label) + '</em>' : '<em class="ai-model-effective">実効: ' + escapeHtml(selected) + '</em>') + '</label>';
      };
      const imageOptions = imageModels.map(function (item) {
        return '<option value="' + escapeAttr(item.id) + '"' + (item.id === current.image_model ? " selected" : "") + '>' + escapeHtml(item.display_name) + '</option>';
      }).join("");
      const reasoningOptions = (registry.reasoning_levels || []).map(function (item) {
        return '<option value="' + escapeAttr(item.id) + '"' + (item.id === current.reasoning_effort ? " selected" : "") + '>' + escapeHtml(item.label) + '</option>';
      }).join("");
      const astra = statusInfo("gpt-6-astra");
      rootNode.innerHTML = '<form class="ai-model-form" id="ai-model-form"><div class="ai-model-preset-row"><label class="editor-label"><span>プリセット</span><select aria-label="プリセット" data-ai-preset>' + presetOptions + '</select></label><div class="ai-model-preset-help">' + escapeHtml((registry.presets || []).find(function (item) { return item.id === current.preset; })?.description || "工程ごとにモデルを選択します。") + '</div></div><div class="ai-availability-note"><span class="availability-dot availability-' + escapeAttr(astra.status) + '"></span><strong>GPT-6 Astra</strong><span>' + escapeHtml(astra.label) + '。アカウントの権限により、利用時はGPT-5.6 Solへ自動フォールバックします。</span></div><details class="ai-advanced"><summary>工程ごとの詳細設定</summary><div class="ai-model-grid">' + modelSelect("story_analysis_model", "Story Analysis", "物語の構造化解析") + modelSelect("adaptation_model", "Manga Adaptation", "漫画用脚本への変換") + modelSelect("settings_recommendation_model", "漫画化設定の推奨", "Analysisから初期設定を推定") + modelSelect("character_model", "Character Bible", "人物の一貫性設定") + modelSelect("storyboard_model", "Storyboard", "ページ・コマ設計") + modelSelect("qa_model", "Knowledge-aware QA", "原作とKnowledgeの確認") + modelSelect("panel_prompt_model", "Panel Prompt", "コマ画像用Prompt") + '</div><div class="ai-model-bottom-row"><label class="ai-model-field"><span>Image Generation<small>画像生成はテキストモデルと分離</small></span><select aria-label="Image Generationのモデル" data-ai-model-field="image_model">' + imageOptions + '</select><em class="ai-model-effective">実効: GPT-Image-2</em></label><label class="ai-model-field"><span>Reasoning<small>対応モデルでのみ送信</small></span><select aria-label="推論強度" data-ai-model-field="reasoning_effort">' + reasoningOptions + '</select></label></div></details><div class="save-row"><span class="field-help" data-ai-model-status>保存済みの設定は次回の制作にも適用されます。</span><button type="submit" class="primary-button compact-button">AIモデル設定を保存</button></div></form>';
      const form = rootNode.querySelector("#ai-model-form");
      form?.addEventListener("change", function (event) {
        if (event.target.matches("[data-ai-preset]") && event.target.value !== "auto") {
          form.querySelectorAll("[data-ai-model-field]").forEach(function (field) {
            if (field.dataset.aiModelField.endsWith("_model") && field.dataset.aiModelField !== "image_model") field.value = "auto";
          });
        }
      });
      form?.addEventListener("submit", async function (event) {
        event.preventDefault();
        const button = form.querySelector("button[type=submit]");
        const status = form.querySelector("[data-ai-model-status]");
        if (!button || button.disabled) return;
        const payload = { preset: form.querySelector("[data-ai-preset]")?.value || "auto" };
        form.querySelectorAll("[data-ai-model-field]").forEach(function (field) { payload[field.dataset.aiModelField] = field.value; });
        button.disabled = true;
        button.textContent = "保存中…";
        if (status) status.textContent = "保存しています…";
        try {
          const response = await fetch("/api/settings/ai-models", { method: "PUT", headers: { Accept: "application/json", "Content-Type": "application/json" }, body: JSON.stringify(payload) });
          const data = await response.json().catch(function () { return {}; });
          if (!response.ok) throw new Error(data.detail || "AIモデル設定を保存できませんでした");
          state = data;
          showToast("AIモデル設定を保存しました");
          render();
        } catch (error) {
          if (status) status.textContent = error.message || "保存に失敗しました";
          showToast(status?.textContent || "保存に失敗しました", "error");
          button.disabled = false;
          button.textContent = "AIモデル設定を保存";
        }
      });
    }

    window.addEventListener("resize", function () {
      if (activeStep === "edit" || activeStep === "preview") scheduleMangaTextFit();
    });
    render();
    fetch("/api/settings/ai-models", { headers: { Accept: "application/json" } }).then(function (response) {
      if (!response.ok) throw new Error("モデルの利用状況を確認できませんでした");
      return response.json();
    }).then(function (data) {
      state = data;
      render();
    }).catch(function () {
      // 可用性確認に失敗しても、保存済み設定の編集は継続できる。
      render();
    });
  }

  function initWorkspace(initial) {
    let state = initial;
    let activeStep = state.current_step || "story";
    let selectedPageIndex = 0;
    let selectedPanelId = null;
    let saveTimer = null;
    let polling = false;
    let panelPollingTimer = null;
    let panelPollingWaitResolve = null;
    let panelPollingAbortController = null;
    let panelPollingRun = 0;
    let panelPollingState = "idle";
    let panelRecoveryNotice = null;
    let panelRecheckInFlight = false;
    let panelStateSyncInFlight = false;
    let panelGenerationState = null;
    let storyboardPolling = false;
    let storyboardJobState = null;
    let storyboardPollRun = 0;
    let characterPolling = false;
    let characterJobState = null;
    let characterPollRun = 0;
    let characterPollingTimer = null;
    let characterPollingWaitResolve = null;
    let characterPollingState = "idle";
    let characterRecoveryNotice = null;
    let characterStateSyncInFlight = false;
    let knowledgeRequestId = 0;
    let qaKnowledgeWarningOpen = false;
    let recommendationLoading = false;
    let recommendationAttempted = false;
    let pendingRecommendation = null;
    const content = document.getElementById("workspace-content");
    if (!content) return;

    const stepOrder = ["story", "knowledge", "analysis", "settings", "characters", "storyboard", "generate", "edit", "qa", "preview", "export"];
    const stepLabels = { story: "物語", knowledge: "Knowledge", analysis: "解析", settings: "漫画化設定", characters: "キャラクター", storyboard: "ネーム", generate: "コマ生成", edit: "編集", qa: "QA", preview: "プレビュー", export: "書き出し" };
    const panelStatusLabels = { not_started: "未生成", queued: "待機中", processing: "生成中", completed: "生成済み", failed: "要再試行" };

    document.querySelectorAll("[data-step-nav]").forEach(function (button) {
      button.addEventListener("click", function () { goToStep(button.dataset.stepNav); });
    });
    document.querySelectorAll("[data-step-action]").forEach(function (button) {
      button.addEventListener("click", function () { goToStep(button.dataset.stepAction); });
    });
    document.addEventListener("keydown", function (event) {
      if (activeStep !== "preview" || processingDialogOpen || event.defaultPrevented) return;
      if (["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(document.activeElement?.tagName)) return;
      const pages = state.storyboard || [];
      if (event.key === "ArrowLeft") {
        selectedPageIndex += canonicalLanguage(state.settings) === "en" ? -1 : 1;
      } else if (event.key === "ArrowRight") {
        selectedPageIndex += canonicalLanguage(state.settings) === "en" ? 1 : -1;
      } else {
        return;
      }
      selectedPageIndex = Math.max(0, Math.min(selectedPageIndex, pages.length - 1));
      event.preventDefault();
      render();
    });
    document.querySelector("[data-rename-project]")?.addEventListener("click", async function () {
      const nextTitle = window.prompt("新しい作品タイトル", state.title);
      if (nextTitle == null || !nextTitle.trim() || nextTitle.trim() === state.title) return;
      try {
        await saveProject({ title: nextTitle.trim() }, "作品タイトルを変更しました", false);
        document.querySelectorAll("[data-project-title]").forEach(function (node) { node.textContent = state.title; });
      } catch (_error) {}
    });

    function allPanels() {
      return (state.storyboard || []).flatMap(function (page) {
        return (page.panels || []).map(function (panel) { return { page: page, panel: panel }; });
      });
    }

    function canonicalLanguage(settings) {
      return settings && settings.language === "en" ? "en" : "ja";
    }

    function htmlDirection(settings) {
      return canonicalLanguage(settings) === "en" ? "ltr" : "rtl";
    }

    function languageLabel(settings) {
      return canonicalLanguage(settings) === "en" ? "English" : "日本語";
    }

    function readingDirectionLabel(settings) {
      return canonicalLanguage(settings) === "en" ? "Left → Right" : "右 → 左";
    }

    function readingDirectionKey(settings) {
      return canonicalLanguage(settings) === "en" ? "left_to_right" : "right_to_left";
    }

    function panelVisualPosition(index, count, settings) {
      const safeIndex = Math.max(0, Number(index) || 0);
      const safeCount = Math.max(1, Number(count) || 1);
      if (safeCount < 3) return { row: safeIndex + 1, column: 1, columnCount: 1 };
      const logicalColumn = safeIndex % 2;
      const column = canonicalLanguage(settings) === "en" ? logicalColumn + 1 : 2 - logicalColumn;
      return { row: Math.floor(safeIndex / 2) + 1, column: column, columnCount: 2 };
    }

    function bubbleSide(index, settings) {
      const first = canonicalLanguage(settings) === "en" ? "left" : "right";
      const second = first === "left" ? "right" : "left";
      return (Math.max(0, Number(index) || 0) % 2 === 0) ? first : second;
    }

    function percentValue(value, fallback) {
      const number = Number(value);
      return Math.max(0, Math.min(1, Number.isFinite(number) ? number : fallback)) * 100;
    }

    function fallbackPageGeometry(page) {
      const panels = page?.panels || [];
      if (!panels.length) return new Map();
      const gap = 1.4;
      const side = 2.5;
      const top = 2;
      const bottom = 6;
      const rows = [];
      if (panels.length === 1) rows.push([0]);
      else if (panels.length <= 3) panels.forEach(function (_panel, index) { rows.push([index]); });
      else {
        rows.push([0]);
        let index = 1;
        while (index < panels.length) {
          const remaining = panels.length - index;
          const size = remaining === 1 ? 1 : 2;
          rows.push(Array.from({ length: size }, function (_item, offset) { return index + offset; }));
          index += size;
        }
      }
      const weights = rows.map(function (row, index) { return row.length === 1 ? (index === rows.length - 1 ? 1.45 : 1.1) : 0.82; });
      const usableHeight = 100 - top - bottom - gap * Math.max(0, rows.length - 1);
      const totalWeight = weights.reduce(function (sum, value) { return sum + value; }, 0);
      const result = new Map();
      let y = top;
      rows.forEach(function (row, rowIndex) {
        const height = usableHeight * weights[rowIndex] / totalWeight;
        const physical = canonicalLanguage(state.settings) === "en" ? row : [...row].reverse();
        const usableWidth = 100 - side * 2 - gap * Math.max(0, row.length - 1);
        const firstWidth = row.length === 2 ? usableWidth * .54 : usableWidth / row.length;
        let x = side;
        physical.forEach(function (panelIndex, physicalIndex) {
          const width = row.length === 2 ? (physicalIndex === 0 ? firstWidth : usableWidth - firstWidth) : usableWidth / row.length;
          result.set(String(panels[panelIndex]?.id || panelIndex), { x: x / 100, y: y / 100, width: width / 100, height: height / 100, row: rowIndex + 1, column: physicalIndex + 1 });
          x += width + gap;
        });
        y += height + gap;
      });
      return result;
    }

    function pageGeometryMap(page) {
      const items = page?.layout_geometry?.panels;
      if (!Array.isArray(items) || items.length !== (page?.panels || []).length) return fallbackPageGeometry(page);
      return new Map(items.map(function (item) { return [String(item.panel_id), item]; }));
    }

    function fallbackTextLayout(panel) {
      const result = [];
      const language = canonicalLanguage(state.settings);
      const sides = language === "en" ? ["left", "right"] : ["right", "left"];
      [["bubble", panel.dialogue || []], ["narration", panel.narration || []], ["sfx", panel.sfx || []]].forEach(function (entry) {
        entry[1].filter(Boolean).forEach(function (text, index) {
          const side = sides[index % 2];
          const row = result.length;
          result.push({ id: entry[0] + "-" + (index + 1), type: entry[0], order: index + 1, text: text, x: side === "left" ? .06 : .56, y: Math.min(.78, .06 + row * .18), width: .38, height: .14, side: side, line_count: 2, font_scale: 1 });
        });
      });
      return result;
    }

    function panelTextLayout(panel) {
      const items = panel?.text_layout?.items;
      return Array.isArray(items) ? items : fallbackTextLayout(panel || {});
    }

    function fitMangaText() {
      document.querySelectorAll(".speech-bubble,.page-narration,.page-sfx").forEach(function (element) {
        element.style.fontSize = "";
        let size = Number.parseFloat(window.getComputedStyle(element).fontSize) || 10;
        while ((element.scrollHeight > element.clientHeight + 1 || element.scrollWidth > element.clientWidth + 1) && size > 5) {
          size = Math.max(5, size - .5);
          element.style.fontSize = size + "px";
        }
        const overflow = element.scrollHeight > element.clientHeight + 1 || element.scrollWidth > element.clientWidth + 1;
        element.dataset.textOverflow = overflow ? "true" : "false";
        element.title = overflow ? "文字が収まらないため、配置の再計算または文面の分割が必要です" : "";
      });
    }

    function scheduleMangaTextFit() {
      window.requestAnimationFrame(fitMangaText);
    }

    function uuid(prefix) {
      if (window.crypto?.randomUUID) return prefix + "-" + window.crypto.randomUUID();
      return prefix + "-" + Date.now() + "-" + Math.random().toString(16).slice(2);
    }

    function textAreaValue(value) {
      return Array.isArray(value) ? value.join("\n") : String(value || "");
    }

    function listFromText(value) {
      return String(value || "").split(/\n/).map(function (line) { return line.trim(); }).filter(Boolean);
    }

    function statusLabel(status) {
      const labels = { draft: "下書き", analysis_ready: "解析済み", characters_ready: "人物設定済み", storyboard_ready: "ネーム準備済み", processing: "生成中", partially_failed: "一部エラー", completed: "完成に近い" };
      return labels[status] || "下書き";
    }

    function generatedCount() {
      return allPanels().filter(function (item) { return item.panel.generation_status === "completed"; }).length;
    }

    function setSaveState(label, saving) {
      document.querySelectorAll("[data-save-state]").forEach(function (node) {
        node.textContent = label;
        node.classList.toggle("saving", Boolean(saving));
      });
    }

    class WorkspaceApiError extends Error {
      constructor(message, status, category, cause) {
        super(message);
        this.name = "WorkspaceApiError";
        this.status = Number(status || 0);
        this.category = category || "http";
        this.cause = cause || null;
      }
    }

    function httpErrorCategory(status) {
      if (status === 401) return "auth";
      if (status === 403) return "forbidden";
      if (status === 404) return "not_found";
      if (status === 408) return "timeout";
      if (status === 429) return "rate_limit";
      if (status >= 500) return "server";
      return "http";
    }

    async function api(url, options) {
      const request = { ...(options || {}) };
      const timeoutMs = Number(request.timeoutMs || 0);
      const externalSignal = request.signal;
      delete request.timeoutMs;
      delete request.signal;
      const controller = typeof AbortController === "function" ? new AbortController() : null;
      const headers = { Accept: "application/json", "Content-Type": "application/json", ...(request.headers || {}) };
      delete request.headers;
      let timeoutId = null;
      let externalAbort = null;
      if (controller) {
        request.signal = controller.signal;
        externalAbort = function () { controller.abort(); };
        if (externalSignal?.aborted) controller.abort();
        else externalSignal?.addEventListener("abort", externalAbort, { once: true });
      }
      if (controller && timeoutMs > 0) timeoutId = window.setTimeout(function () { controller.abort(); }, timeoutMs);
      try {
        let response;
        try {
          response = await fetch(url, { ...request, headers: headers });
        } catch (error) {
          const timedOut = error?.name === "AbortError" && timeoutMs > 0;
          throw new WorkspaceApiError(
            timedOut ? "サーバーとの通信がタイムアウトしました" : "サーバーとの接続に失敗しました",
            0,
            timedOut ? "timeout" : "network",
            error
          );
        }
        const body = await response.text();
        let data = {};
        if (body.trim()) {
          try {
            data = JSON.parse(body);
          } catch (error) {
            if (response.ok) throw new WorkspaceApiError("サーバーから不正な応答を受け取りました", response.status, "invalid_response", error);
          }
        }
        if (!response.ok) {
          const fallback = response.status === 401
            ? "ログイン状態を確認してください"
            : response.status === 403
              ? "このProjectへのアクセス権を確認できません"
              : response.status === 404
                ? "対象のProjectまたはJobが見つかりません"
                : "サーバーとの通信に失敗しました";
          throw new WorkspaceApiError(data && data.detail ? data.detail : fallback, response.status, httpErrorCategory(response.status));
        }
        return data;
      } finally {
        if (timeoutId) window.clearTimeout(timeoutId);
        if (externalSignal && externalAbort) externalSignal.removeEventListener("abort", externalAbort);
      }
    }

    async function fetchProject(shouldRender) {
      const data = await api("/api/projects/" + encodeURIComponent(state.id));
      state = data.project;
      if (shouldRender !== false) render();
      return state;
    }

    async function saveProject(patch, message, shouldRender) {
      setSaveState("保存中", true);
      try {
        const data = await api("/api/projects/" + encodeURIComponent(state.id), { method: "PATCH", body: JSON.stringify(patch) });
        state = data.project;
        setSaveState("保存済み", false);
        if (message) showToast(message);
        if (shouldRender !== false) render();
        return state;
      } catch (error) {
        setSaveState("保存エラー", false);
        showToast(error.message, "error");
        throw error;
      }
    }

    function recommendationSettings(value) {
      const recommendation = value || {};
      return {
        target_page_count: recommendation.recommended_page_count,
        color_mode: recommendation.recommended_color_mode,
        visual_style: recommendation.recommended_visual_style,
        pacing: recommendation.recommended_pacing,
        dialogue_density: recommendation.recommended_dialogue_density,
        target_audience: recommendation.recommended_target_audience
      };
    }

    async function loadSettingsRecommendation(force) {
      if (recommendationLoading || !state.analysis) return;
      recommendationLoading = true;
      showProcessingDialog({
        message: "漫画化設定を最適化しています…",
        progress: force ? "分析結果から新しい推奨値を作成しています" : "シナリオの複雑度とページ配分を確認しています",
        submessage: "推奨値は確認・編集してから保存できます。"
      });
      try {
        const data = await api("/api/projects/" + encodeURIComponent(state.id) + "/settings/recommendation", { method: "POST", body: JSON.stringify({ force: Boolean(force) }) });
        state = data.project;
        pendingRecommendation = force ? data.recommendation : null;
        if (data.fallback) showToast("AI推奨を取得できなかったため、分析結果から標準推奨を表示しています。", "error");
        render();
      } catch (error) {
        showToast(error.message || "AI推奨を取得できませんでした", "error");
      } finally {
        recommendationLoading = false;
        hideProcessingDialog();
        if (activeStep === "settings") render();
      }
    }

    function scheduleAutosave(patch) {
      window.clearTimeout(saveTimer);
      setSaveState("変更を保存中", true);
      saveTimer = window.setTimeout(function () { saveProject(patch, null, false).catch(function () {}); }, 650);
    }

    async function goToStep(step) {
      if (!stepOrder.includes(step)) return;
      activeStep = step;
      await saveProject({ current_step: step }, null, false).catch(function () {});
      render();
      if (step === "characters") restoreCharacterJobState();
      if (step === "generate") restorePanelGenerationState();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function renderProgress() {
      const hasAnalysis = Boolean(state.analysis);
      const hasCharacters = (state.characters || []).length > 0;
      const hasStoryboard = (state.storyboard || []).length > 0;
      const hasGenerated = generatedCount() > 0;
      const completed = { story: Boolean(state.original_text), knowledge: (state.knowledge || []).some(function (item) { return item.enabled; }), analysis: hasAnalysis, settings: Boolean(state.settings), characters: hasCharacters, storyboard: hasStoryboard, generate: hasGenerated, edit: hasGenerated, qa: Boolean(state.quality_check), preview: hasGenerated, export: hasGenerated };
      document.querySelectorAll("[data-step-nav]").forEach(function (button) {
        const step = button.dataset.stepNav;
        button.classList.toggle("active", step === activeStep);
        button.classList.toggle("complete", completed[step]);
        button.setAttribute("aria-current", step === activeStep ? "step" : "false");
      });
      const status = document.querySelector("[data-project-status]");
      if (status) status.textContent = statusLabel(state.status);
    }

    function heading(title, description, actions) {
      const event = [...(state.generation_metadata || [])].reverse().find(function (item) { return item && item.actual_model && ["analysis", "characters", "storyboard", "qa"].some(function (task) { return String(item.task || "").includes(task); }); });
      const modelNote = event ? '<span class="generation-model-note">実行モデル: ' + escapeHtml(event.actual_model) + (event.fallback ? '（AstraからSolへフォールバック）' : '') + '</span>' : '';
      return '<div class="workspace-heading"><div><p class="eyebrow">' + escapeHtml(stepLabels[activeStep] || "WORKSPACE") + '</p><h2>' + escapeHtml(title) + '</h2><p>' + escapeHtml(description) + '</p>' + modelNote + '</div>' + (actions ? '<div class="workspace-heading-actions">' + actions + '</div>' : "") + '</div>';
    }

    function nextButton(step, label) {
      return '<div class="save-row"><button type="button" class="primary-button compact-button" data-next-step="' + step + '">' + escapeHtml(label) + ' <span aria-hidden="true">→</span></button></div>';
    }

    function renderStory() {
      const story = state.original_text || "";
      const analysisPreview = state.analysis ? '<div class="callout"><p><strong>解析済み。</strong> 次は内容を確認して、漫画化設定を決められます。</p></div>' : '<div class="callout"><p>本文の内容は命令として実行せず、作品の参照資料として解析します。</p></div>';
      const storyAction = state.analysis ? '<button type="button" class="primary-button compact-button" data-go-to-analysis>解析を確認する <span aria-hidden="true">→</span></button>' : '<button type="button" class="primary-button compact-button" data-go-to-knowledge>Knowledgeを設定する <span aria-hidden="true">→</span></button>';
      content.innerHTML = heading("物語を確認する", "原作本文を保ったまま、次の工程で漫画向けに整理します。") + '<div class="section-grid"><section class="surface-panel panel-padding"><div class="source-meta"><span>' + escapeHtml(state.source_type === "text" ? "直接入力" : state.source_type.toUpperCase()) + '</span><span>' + escapeHtml(state.source_filename || "本文") + '</span><span>' + story.length.toLocaleString("ja-JP") + '文字</span></div><h3>本文</h3><p class="panel-lead">抽出結果を読み、必要ならここで修正してください。</p><textarea class="editor-textarea story-editor" rows="18" aria-label="物語本文">' + escapeHtml(story) + '</textarea><div class="save-row"><button type="button" class="secondary-button compact-button" data-save-story>本文を保存</button>' + storyAction + '</div></section><aside class="surface-panel panel-padding"><h3>このProjectで進めること</h3><div class="stat-rail"><div class="stat-tile"><span>ページ構成</span><strong>' + escapeHtml(state.settings?.target_page_count || 8) + '</strong></div><div class="stat-tile"><span>現在のコマ</span><strong>' + escapeHtml(allPanels().length) + '</strong></div><div class="stat-tile"><span>生成済み</span><strong>' + escapeHtml(generatedCount()) + '</strong></div></div>' + analysisPreview + '</aside></div>' + nextButton(state.analysis ? "analysis" : "knowledge", state.analysis ? "解析を確認する" : "Knowledgeを設定する");
      content.querySelector("[data-save-story]").addEventListener("click", function () {
        saveProject({ original_text: content.querySelector(".story-editor").value }, "本文を保存しました", false);
      });
      content.querySelector("[data-go-to-analysis]")?.addEventListener("click", function () { goToStep("analysis"); });
      content.querySelector("[data-go-to-knowledge]")?.addEventListener("click", function () { goToStep("knowledge"); });
      content.querySelector("[data-next-step]").addEventListener("click", async function () {
        if (state.analysis) goToStep("analysis"); else goToStep("knowledge");
      });
    }

    async function generateAnalysis() {
      const button = content.querySelector("[data-generate-analysis]");
      if (button) { button.disabled = true; button.textContent = "物語を解析中…"; }
      setSaveState("解析中", true);
      showProcessingDialog({
        message: "物語を解析しています…",
        progress: "物語の構造と重要な出来事を整理しています",
        submessage: "原作と選択したKnowledgeを参照しています。"
      });
      try {
        const data = await api("/api/projects/" + encodeURIComponent(state.id) + "/analysis", { method: "POST", body: "{}" });
        state = data.project;
        showToast("物語の解析ができました。内容を確認してください");
        activeStep = "analysis";
        render();
      } catch (error) {
        showToast(error.message, "error");
        if (button) { button.disabled = false; button.textContent = "解析を始める →"; }
      } finally {
        hideProcessingDialog();
        setSaveState("保存済み", false);
      }
    }

    function renderAnalysis() {
      const analysis = state.analysis || {};
      const field = function (key, label, value, full, multiline) {
        return '<label class="editor-label ' + (full ? "full" : "") + '">' + escapeHtml(label) + '<textarea data-analysis-field="' + key + '" class="editor-textarea ' + (multiline ? "analysis-list" : "") + '" rows="' + (multiline ? "4" : "3") + '">' + escapeHtml(Array.isArray(value) ? textAreaValue(value) : value || "") + '</textarea></label>';
      };
      const scalar = function (key, label, value) { return '<label class="editor-label">' + escapeHtml(label) + '<input data-analysis-field="' + key + '" class="editor-input" value="' + escapeAttr(value || "") + '"></label>'; };
      content.innerHTML = heading("解析を編集する", "AIの読み取りを確認し、原作と違うところはここで直します。") + (state.analysis ? '<section class="surface-panel panel-padding"><div class="analysis-grid">' + scalar("title", "解析上のタイトル", analysis.title) + scalar("genre", "ジャンル", analysis.genre) + scalar("tone", "トーン", analysis.tone) + field("synopsis", "あらすじ", analysis.synopsis, true) + field("world_setting", "世界観・舞台", analysis.world_setting, true) + field("main_characters", "主要人物", analysis.main_characters, false, true) + field("supporting_characters", "脇役", analysis.supporting_characters, false, true) + field("locations", "場所", analysis.locations, false, true) + field("major_events", "主な出来事", analysis.major_events, false, true) + field("story_beats", "ストーリービート", analysis.story_beats, false, true) + field("conflicts", "対立・葛藤", analysis.conflicts, false, true) + field("important_objects", "重要な物", analysis.important_objects, false, true) + field("climax", "クライマックス", analysis.climax, true) + field("ending", "結末", analysis.ending, true) + '</div><div class="save-row"><button type="button" class="secondary-button compact-button" data-regenerate-analysis>解析をやり直す</button><button type="button" class="primary-button compact-button" data-save-analysis>解析を保存</button></div></section>' + nextButton("settings", "漫画化設定へ") : '<section class="surface-panel empty-panel"><h3>まず物語を解析しましょう</h3><p>原作の要素を編集可能な項目へ整理します。</p><button type="button" class="primary-button compact-button" data-generate-analysis>解析を始める</button></section>');
      content.querySelector("[data-generate-analysis]")?.addEventListener("click", generateAnalysis);
      content.querySelector("[data-regenerate-analysis]")?.addEventListener("click", generateAnalysis);
      content.querySelector("[data-save-analysis]")?.addEventListener("click", function () {
        const next = { ...analysis };
        content.querySelectorAll("[data-analysis-field]").forEach(function (input) {
          const key = input.dataset.analysisField;
          next[key] = input.tagName === "TEXTAREA" && ["main_characters", "supporting_characters", "locations", "major_events", "story_beats", "conflicts", "important_objects"].includes(key) ? listFromText(input.value) : input.value;
        });
        saveProject({ analysis: next, current_step: "analysis" }, "解析を保存しました", true);
      });
      content.querySelector("[data-next-step]")?.addEventListener("click", function () { goToStep("settings"); });
    }

    const knowledgeScopeLabels = {
      all: "すべての工程",
      story_analysis: "物語解析",
      adaptation: "漫画化脚本",
      character: "キャラクター",
      storyboard: "ネーム",
      page_layout: "ページレイアウト",
      panel_prompt: "コマPrompt",
      dialogue: "セリフ",
      image_generation: "画像生成",
      quality_check: "品質確認",
      export: "書き出し",
    };

    function renderKnowledge() {
      const requestId = ++knowledgeRequestId;
      content.innerHTML = heading("ProjectのKnowledgeを選ぶ", "使うDocument、Versionの追従方法、参照する工程を設定します。") + '<div class="loading-state"><div class="loading-bar"></div><p>Knowledge一覧を読み込んでいます</p></div>';
      api("/api/knowledge").then(function (data) {
        if (requestId !== knowledgeRequestId || activeStep !== "knowledge") return;
        const documents = data.knowledge || [];
        const selectedById = Object.fromEntries((state.knowledge || []).map(function (item) { return [item.knowledge_document_id, item]; }));
        const scopeOptions = Object.entries(knowledgeScopeLabels).map(function (entry) { return '<option value="' + escapeAttr(entry[0]) + '">' + escapeHtml(entry[1]) + '</option>'; }).join("");
        const cards = documents.map(function (document) {
          const selected = selectedById[document.id] || {};
          const archived = Boolean(document.archived);
          const enabled = !archived && Boolean(selected.enabled);
          const fieldDisabled = archived ? " disabled" : "";
          const mode = selected.mode || "follow_latest";
          const versions = document.versions || [];
          const selectedVersion = selected.selected_version_id || document.active_version_id || versions[0]?.id || "";
          const scopes = selected.scope || document.default_scope || ["all"];
          const versionOptions = versions.length ? versions.map(function (version) { return '<option value="' + escapeAttr(version.id) + '"' + (version.id === selectedVersion ? " selected" : "") + '>Version ' + escapeHtml(version.version_number) + ' (' + escapeHtml(version.status) + ')</option>'; }).join("") : '<option value="">利用可能なVersionなし</option>';
          const scopeOptionMarkup = scopeOptions.replace(/<option value="([^"]+)">/g, function (_match, value) { return '<option value="' + value + '"' + (scopes.includes(value) ? " selected" : "") + '>'; });
          return '<article class="knowledge-check surface-panel' + (archived ? " archived" : "") + '" data-knowledge-selection-card data-document-id="' + escapeAttr(document.id) + '"><div class="knowledge-check-header"><label class="knowledge-check-title"><input type="checkbox" data-knowledge-enabled' + (enabled ? " checked" : "") + fieldDisabled + '><span><strong>' + escapeHtml(document.title) + '</strong><small>' + escapeHtml(document.category) + ' / ' + escapeHtml(document.version_count || 0) + ' Version</small></span></label><span class="knowledge-active-label ' + (archived ? "archived" : (document.active ? "" : "inactive")) + '">' + (archived ? "アーカイブ済み" : (document.active ? "有効" : "無効")) + '</span></div><p class="knowledge-check-description">' + escapeHtml(document.description || "説明はまだありません。") + '</p><div class="knowledge-selection-fields"><label class="editor-label">優先度<input type="number" min="0" max="1000" value="' + escapeAttr(selected.priority ?? 50) + '" data-knowledge-priority' + fieldDisabled + '></label><label class="editor-label">Versionの扱い<select data-knowledge-mode' + fieldDisabled + '><option value="follow_latest"' + (mode === "follow_latest" ? " selected" : "") + '>最新Versionに追従</option><option value="pinned"' + (mode === "pinned" ? " selected" : "") + '>Versionを固定</option></select></label><label class="editor-label knowledge-version-field">固定Version<select data-knowledge-version' + (mode === "follow_latest" || archived ? " disabled" : "") + '>' + versionOptions + '</select></label><label class="editor-label knowledge-scope-field">参照Scope<select multiple size="3" data-knowledge-scope aria-label="' + escapeAttr(document.title) + 'の参照Scope"' + fieldDisabled + '>' + scopeOptionMarkup + '</select><small>複数選択できます。未変更ならすべての工程。</small></label></div>' + (archived ? '<p class="field-help">詳細画面からアーカイブを解除するとProjectで選択できます。</p>' : '') + '</article>';
        }).join("");
        const body = documents.length ? '<div class="knowledge-check-list">' + cards + '</div><div class="save-row"><button type="button" class="secondary-button compact-button" data-refresh-knowledge>一覧を再読込</button><button type="button" class="primary-button compact-button" data-save-knowledge>Knowledge設定を保存</button></div>' + nextButton("analysis", "解析へ進む") : '<section class="surface-panel empty-panel knowledge-empty"><h3>Projectで使えるKnowledgeがありません</h3><p>先にKnowledge Libraryへ制作ルールや世界観を登録してください。Knowledgeなしでも解析は進められます。</p><div class="save-row"><a class="secondary-button compact-button" href="/knowledge">Knowledge Libraryを開く</a>' + nextButton("analysis", "Knowledgeなしで解析") + '</div></section>';
        content.innerHTML = heading("ProjectのKnowledgeを選ぶ", "使うDocument、Versionの追従方法、参照する工程を設定します。") + '<div class="knowledge-workspace-note"><strong>参照資料として使う</strong><span>本文中の命令文は実行せず、選択したScopeの制作ルールだけをAI処理へ渡します。</span></div>' + body;
        content.querySelectorAll("[data-knowledge-mode]").forEach(function (select) {
          select.addEventListener("change", function () {
            const card = select.closest("[data-knowledge-selection-card]");
            const version = card?.querySelector("[data-knowledge-version]");
            if (version) version.disabled = select.value !== "pinned";
          });
        });
        content.querySelector("[data-save-knowledge]")?.addEventListener("click", saveKnowledgeSelections);
        content.querySelector("[data-refresh-knowledge]")?.addEventListener("click", renderKnowledge);
        content.querySelector("[data-next-step]")?.addEventListener("click", function () { if (state.analysis) goToStep("analysis"); else generateAnalysis(); });
      }).catch(function (error) {
        if (requestId !== knowledgeRequestId || activeStep !== "knowledge") return;
        content.innerHTML = heading("ProjectのKnowledgeを選ぶ", "使うDocument、Versionの追従方法、参照する工程を設定します。") + '<section class="surface-panel empty-panel"><h3>Knowledge一覧を読み込めませんでした</h3><p>' + escapeHtml(error.message) + '</p><button type="button" class="primary-button compact-button" data-retry-knowledge>再試行</button></section>';
        content.querySelector("[data-retry-knowledge]")?.addEventListener("click", renderKnowledge);
      });
    }

    async function saveKnowledgeSelections() {
      const button = content.querySelector("[data-save-knowledge]");
      if (!button || button.disabled) return;
      const selections = [];
      let invalid = "";
      content.querySelectorAll("[data-knowledge-selection-card]").forEach(function (card) {
        const enabled = card.querySelector("[data-knowledge-enabled]")?.checked;
        if (card.classList.contains("archived")) return;
        const mode = card.querySelector("[data-knowledge-mode]")?.value || "follow_latest";
        const version = card.querySelector("[data-knowledge-version]")?.value || null;
        if (enabled && mode === "pinned" && !version) invalid = "固定するVersionを選択してください";
        const priority = Number(card.querySelector("[data-knowledge-priority]")?.value || 50);
        const scope = Array.from(card.querySelector("[data-knowledge-scope]")?.selectedOptions || []).map(function (option) { return option.value; });
        selections.push({ knowledge_document_id: card.dataset.documentId, enabled: Boolean(enabled), priority: Number.isFinite(priority) ? priority : 50, mode: mode, selected_version_id: mode === "pinned" ? version : null, scope: scope.length ? scope : ["all"] });
      });
      if (invalid) { showToast(invalid, "error"); return; }
      button.disabled = true;
      const original = button.textContent;
      button.textContent = "保存中…";
      setSaveState("Knowledge保存中", true);
      try {
        const data = await api("/api/projects/" + encodeURIComponent(state.id) + "/knowledge", { method: "PUT", body: JSON.stringify({ selections: selections }) });
        state = data.project;
        setSaveState("保存済み", false);
        showToast("ProjectのKnowledge設定を保存しました");
        render();
      } catch (error) {
        setSaveState("保存エラー", false);
        showToast(error.message, "error");
        button.disabled = false;
        button.textContent = original;
      }
    }

    function selectField(key, label, value, options) {
      return '<label class="editor-label">' + escapeHtml(label) + '<select data-settings-field="' + key + '">' + options.map(function (option) { return '<option value="' + escapeAttr(option.value) + '"' + (option.value === value ? " selected" : "") + '>' + escapeHtml(option.label) + '</option>'; }).join("") + '</select></label>';
    }

    function renderSettings() {
      const savedSettings = state.settings || {};
      const storedRecommendation = state.manga_settings_recommendation;
      const recommendation = storedRecommendation && !storedRecommendation.user_override && !storedRecommendation.stale && !pendingRecommendation ? storedRecommendation : null;
      const settings = { ...savedSettings, ...(recommendation ? recommendationSettings(recommendation) : {}) };
      const styleOptions = [{ value: "dynamic", label: "動きのある少年漫画風" }, { value: "elegant", label: "繊細で余白のある演出" }, { value: "cinematic", label: "映画的な陰影" }, { value: "comedy", label: "表情豊かなコメディ" }, { value: "minimal", label: "線と余白のミニマル" }, { value: "webtoon", label: "縦読み向けの明快さ" }];
      const currentLanguage = canonicalLanguage(settings);
      const languageOptions = [{ value: "ja", label: "日本語" }, { value: "en", label: "English" }];
      const direction = readingDirectionLabel(settings);
      const budget = recommendation?.scene_page_budget || [];
      const budgetText = budget.slice(0, 8).map(function (item) { return '<span>' + escapeHtml(item.scene || "シーン") + ' ' + escapeHtml(item.estimated_pages || 1) + 'p</span>'; }).join("");
      const fallbackNotice = storedRecommendation?.fallback ? '<div class="form-notice recommendation-fallback"><span class="notice-mark">!</span><p>AIによる推奨値を取得できなかったため、既存の分析結果から標準推奨を表示しています。</p></div>' : '';
      const staleNotice = storedRecommendation?.stale ? '<div class="form-notice recommendation-stale"><span class="notice-mark">!</span><p>シナリオ分析が更新されています。現在の設定は保持したまま、必要なら再提案してください。</p></div>' : '';
      const preview = pendingRecommendation ? '<div class="recommendation-preview" role="status"><div><strong>再提案のプレビュー</strong><p>' + escapeHtml(pendingRecommendation.page_count_reason || pendingRecommendation.recommendation_reason || "分析結果から新しい推奨値を作成しました。") + '</p></div><div class="save-row"><button type="button" class="secondary-button compact-button" data-cancel-recommendation>現在の設定を維持</button><button type="button" class="primary-button compact-button" data-apply-recommendation>推奨値を適用</button></div></div>' : '';
      const recommendationRetry = recommendationAttempted ? '<button type="button" class="secondary-button compact-button" data-retry-recommendation>AI推奨を再試行</button>' : '';
      const recommendationPanel = storedRecommendation ? '<section class="recommendation-panel" aria-live="polite"><div class="recommendation-header"><div><span class="settings-badge">AI推奨</span><strong>シナリオ分析からの初期提案</strong></div><button type="button" class="text-button" data-refresh-recommendation' + (recommendationLoading ? ' disabled' : '') + '>分析結果から再提案</button></div><p>' + escapeHtml(storedRecommendation.page_count_reason || storedRecommendation.recommendation_reason || "分析結果に基づく漫画化設定です。") + '</p>' + (budgetText ? '<div class="recommendation-budget" aria-label="シーン別ページ配分">' + budgetText + '</div>' : '') + fallbackNotice + staleNotice + preview + '</section>' : '<section class="recommendation-panel recommendation-empty" aria-live="polite"><div><span class="settings-badge">AI推奨</span><strong>分析結果から初期値を作成します</strong></div><p>目標ページ数、テンポ、画面スタイルなどを既存のStory Analysisから推定します。</p>' + recommendationRetry + '</section>';
      content.innerHTML = heading("漫画化の方針を決める", "AIが提案するページ構成とコマの雰囲気をここで指定します。") + recommendationPanel + '<section class="surface-panel panel-padding"><div class="settings-grid"><label class="editor-label">目標ページ数<small>' + (recommendation ? 'AI推奨値。保存前に自由に変更できます。' : 'デモ生成では最大8ページまで作成します。') + '</small><input type="number" min="1" max="120" data-settings-field="target_page_count" value="' + escapeAttr(settings.target_page_count || 8) + '"></label>' + selectField("language", "漫画の言語", currentLanguage, languageOptions) + '<div class="editor-label settings-direction-readonly"><span>読み方向</span><strong data-reading-direction>' + escapeHtml(direction) + '</strong><small>言語により自動設定されます</small></div>' + selectField("color_mode", "色", settings.color_mode || "bw", [{ value: "bw", label: "白黒" }, { value: "color", label: "カラー" }]) + selectField("visual_style", "視覚スタイル", settings.visual_style || "cinematic", styleOptions) + selectField("pacing", "テンポ", settings.pacing || "balanced", [{ value: "fast", label: "速め" }, { value: "balanced", label: "標準" }, { value: "slow", label: "余韻を長く" }]) + selectField("dialogue_density", "セリフ量", settings.dialogue_density || "medium", [{ value: "low", label: "少なめ" }, { value: "medium", label: "標準" }, { value: "high", label: "多め" }]) + '<label class="editor-label full">想定読者<input data-settings-field="target_audience" value="' + escapeAttr(settings.target_audience || "一般読者") + '"></label></div><div class="form-notice"><span class="notice-mark">i</span><p>作家名や作品名を指定して模倣するのではなく、画面の性質としてスタイルを選びます。</p></div><div class="form-notice language-change-warning" data-language-warning hidden><span class="notice-mark">!</span><p>言語を変更すると、読順・コマ順・吹き出し配置が変更されます。既存画像やセリフ本文は自動翻訳されません。</p></div>' + (recommendation ? '<div class="field-help recommendation-applied-note">表示中の値はAI推奨を反映しています。保存した設定は次回以降自動上書きされません。</div>' : '') + '<div class="save-row"><button type="button" class="primary-button compact-button" data-save-settings>設定を保存</button></div></section>' + nextButton("characters", "キャラクター設定へ");
      const languageSelect = content.querySelector('[data-settings-field="language"]');
      const directionNode = content.querySelector("[data-reading-direction]");
      const warning = content.querySelector("[data-language-warning]");
      languageSelect?.addEventListener("change", function () {
        if (directionNode) directionNode.textContent = readingDirectionLabel({ language: languageSelect.value });
        if (warning) warning.hidden = languageSelect.value === currentLanguage;
      });
      content.querySelector("[data-refresh-recommendation]")?.addEventListener("click", function () { loadSettingsRecommendation(true); });
      content.querySelector("[data-retry-recommendation]")?.addEventListener("click", function () { loadSettingsRecommendation(false); });
      content.querySelector("[data-cancel-recommendation]")?.addEventListener("click", function () { pendingRecommendation = null; render(); });
      content.querySelector("[data-apply-recommendation]")?.addEventListener("click", async function (event) {
        const button = event.currentTarget;
        if (!pendingRecommendation || button.disabled) return;
        button.disabled = true;
        const next = { ...savedSettings, ...recommendationSettings(pendingRecommendation) };
        delete next.reading_direction;
        try {
          pendingRecommendation = null;
          await saveProject({ settings: next, current_step: "settings" }, "AI推奨を適用しました", true);
        } catch (_error) { button.disabled = false; }
      });
      content.querySelector("[data-save-settings]").addEventListener("click", function () {
        const next = { ...settings };
        content.querySelectorAll("[data-settings-field]").forEach(function (input) { next[input.dataset.settingsField] = input.type === "number" ? Number(input.value) : input.value; });
        delete next.reading_direction;
        saveProject({ settings: next, current_step: "settings" }, "漫画化設定を保存しました", true);
      });
      content.querySelector("[data-next-step]").addEventListener("click", function () { goToStep("characters"); });
      if (state.analysis && !storedRecommendation && !recommendationLoading && !recommendationAttempted) {
        recommendationAttempted = true;
        loadSettingsRecommendation(false);
      }
    }

    function characterStatusUrl() {
      return "/api/projects/" + encodeURIComponent(state.id) + "/generation/status";
    }

    function clearCharacterPollingTimer() {
      if (characterPollingTimer) window.clearTimeout(characterPollingTimer);
      characterPollingTimer = null;
      if (characterPollingWaitResolve) characterPollingWaitResolve(false);
      characterPollingWaitResolve = null;
    }

    function cancelCharacterPolling() {
      characterPollRun += 1;
      clearCharacterPollingTimer();
      characterPolling = false;
      characterPollingState = "idle";
    }

    function waitForCharacterPoll(delay, runId) {
      return new Promise(function (resolve) {
        clearCharacterPollingTimer();
        characterPollingWaitResolve = resolve;
        characterPollingTimer = window.setTimeout(function () {
          characterPollingTimer = null;
          characterPollingWaitResolve = null;
          resolve(runId === characterPollRun);
        }, delay);
      });
    }

    function characterRecoveryKind(error) {
      if (error?.status === 401 || error?.category === "auth") return "auth";
      if (error?.status === 403 || error?.category === "forbidden") return "forbidden";
      if (error?.status === 404 || error?.category === "not_found") return "not_found";
      if (error?.category === "timeout") return "timeout";
      return "connection";
    }

    function showCharacterRecoveryNotice(kind) {
      const notices = {
        connection: { message: "人物設定の処理状況を確認できません。", detail: "一時的な通信エラーです。Jobを停止したとは判断していません。" },
        timeout: { message: "人物設定の確認がタイムアウトしました。", detail: "保存済みのJob状態を再確認できます。" },
        not_found: { message: "人物設定のJobを見つけられませんでした。", detail: "Projectを再取得して保存済みの人物設定を確認してください。" },
        auth: { message: "ログイン状態を確認できません。", detail: "再ログイン後に人物設定の状態を再確認してください。" },
        forbidden: { message: "Projectへのアクセス権を確認できません。", detail: "Projectの所有者アカウントで再ログインしてください。" }
      };
      const notice = notices[kind] || notices.connection;
      hideProcessingDialog();
      characterPollingState = kind === "auth" || kind === "forbidden" ? "connection_error" : "unknown_recoverable";
      characterJobState = characterJobState
        ? { ...characterJobState, status: "unknown" }
        : { status: "unknown" };
      characterRecoveryNotice = { kind: kind, message: notice.message, detail: notice.detail };
      if (activeStep === "characters") render();
      showToast(notice.message + " 状態を再確認できます。", "error");
    }

    function renderCharacterRecoveryNotice() {
      if (!characterRecoveryNotice) return "";
      const next = encodeURIComponent(window.location.pathname + window.location.search);
      const loginAction = characterRecoveryNotice.kind === "auth" || characterRecoveryNotice.kind === "forbidden"
        ? '<a class="secondary-button compact-button" href="/login?next=' + escapeAttr(next) + '">再ログイン</a>'
        : "";
      return '<div class="form-notice error-notice generation-recovery-notice" role="alert"><span class="notice-mark">!</span><div><strong>' + escapeHtml(characterRecoveryNotice.message) + '</strong><p>' + escapeHtml(characterRecoveryNotice.detail) + '</p><div class="generation-recovery-actions"><button type="button" class="secondary-button compact-button" data-character-recheck' + (characterStateSyncInFlight ? " disabled" : "") + '>状態を再確認</button><button type="button" class="text-button" data-character-reload>再読み込み</button>' + loginAction + '</div></div></div>';
    }

    async function rediscoverCharacterStateAfterNotFound() {
      try {
        await fetchProject(false);
        const characters = state.characters || [];
        const job = characterJobState;
        if (characters.length || !job || !["queued", "processing"].includes(job.status)) {
          hideProcessingDialog();
          characterRecoveryNotice = null;
          characterPollingState = characters.length ? "completed" : "failed";
          if (activeStep === "characters") render();
          showToast(characters.length ? "保存済みの人物設定を表示しました" : "人物設定の状態を再取得しました");
          return true;
        }
      } catch (_error) {
        // 下の非ブロッキング警告へ進む。
      }
      showCharacterRecoveryNotice("not_found");
      return false;
    }

    async function recheckCharacterGenerationState() {
      if (characterStateSyncInFlight || activeStep !== "characters") return;
      characterStateSyncInFlight = true;
      cancelCharacterPolling();
      hideProcessingDialog();
      try {
        const data = await api(characterStatusUrl(), { timeoutMs: 10000 });
        characterJobState = data.character_job || null;
        if (data.project_status) state.status = data.project_status;
        await fetchProject(false).catch(function () {});
        characterRecoveryNotice = null;
        if (characterJobState && ["queued", "processing"].includes(characterJobState.status)) {
          showProcessingDialog({ message: "キャラクター設定を生成しています…", progress: "サーバー上のJob状態を復元しています", submessage: "確認できた状態に戻して処理を追跡します。" });
          await pollCharacterJob(characterJobState.id, "キャラクター設定を生成しています…");
        } else {
          render();
          showToast("保存済みの人物設定状態を確認しました");
        }
      } catch (error) {
        if (characterRecoveryKind(error) === "not_found") await rediscoverCharacterStateAfterNotFound();
        else showCharacterRecoveryNotice(characterRecoveryKind(error));
      } finally {
        characterStateSyncInFlight = false;
        if (activeStep === "characters" && !characterPolling) render();
      }
    }

    async function pollCharacterJob(jobId, operationMessage) {
      if (characterPolling) return;
      const runId = ++characterPollRun;
      const startedAt = Date.now();
      const maximumPollingMs = 16 * 60 * 1000;
      let consecutiveNetworkErrors = 0;
      let finished = false;
      characterPolling = true;
      characterPollingState = "active";
      characterJobState = { ...(characterJobState || {}), id: jobId, status: "processing" };
      if (activeStep === "characters") render();
      try {
        while (Date.now() - startedAt < maximumPollingMs) {
          const interval = Date.now() - startedAt < 60 * 1000 ? 1500 : 5000;
          if (!await waitForCharacterPoll(interval, runId) || runId !== characterPollRun) return;
          let data;
          try {
            data = await api(characterStatusUrl(), { timeoutMs: 10000 });
            consecutiveNetworkErrors = 0;
          } catch (error) {
            if (runId !== characterPollRun) return;
            consecutiveNetworkErrors += 1;
            characterPollingState = "reconnecting";
            if (consecutiveNetworkErrors >= 4) {
              showCharacterRecoveryNotice(characterRecoveryKind(error) === "timeout" ? "timeout" : "connection");
              return;
            }
            updateProcessingDialog({ message: operationMessage, progress: "通信を再確認しています", submessage: "Jobはサーバー側で継続します。状態の取得を再試行しています。" });
            continue;
          }
          if (runId !== characterPollRun) return;
          const job = data.character_job || (data.jobs || []).find(function (item) { return item.id === jobId && item.job_type === "character"; });
          if (!job || job.id !== jobId) {
            await rediscoverCharacterStateAfterNotFound();
            return;
          }
          characterJobState = job;
          if (data.project_status) state.status = data.project_status;
          if (job.status === "completed") {
            await fetchProject(false);
            characterRecoveryNotice = null;
            characterPollingState = "completed";
            finished = true;
            showToast("キャラクターバイブルを作成しました");
            render();
            return;
          }
          if (job.status === "failed") {
            await fetchProject(false).catch(function () {});
            characterPollingState = "failed";
            finished = true;
            render();
            showToast(job.error || "人物設定の生成に失敗しました", "error");
            return;
          }
          if (job.status !== "queued" && job.status !== "processing") {
            showCharacterRecoveryNotice("connection");
            return;
          }
          updateProcessingDialog({
            message: operationMessage,
            progress: job.status === "queued" ? "生成キューで順番を待っています" : "AIが人物設定を作成しています",
            submessage: "完了後に人物設定を保存します。画面を閉じても処理は継続します。"
          });
          if (activeStep === "characters") render();
        }
        if (runId === characterPollRun) showCharacterRecoveryNotice("timeout");
      } catch (error) {
        if (runId === characterPollRun) showCharacterRecoveryNotice(characterRecoveryKind(error));
      } finally {
        if (runId === characterPollRun) {
          characterPolling = false;
          clearCharacterPollingTimer();
          if (finished) hideProcessingDialog();
          if (activeStep === "characters") render();
        }
      }
    }

    async function restoreCharacterJobState() {
      if (characterStateSyncInFlight || characterPolling) return;
      characterStateSyncInFlight = true;
      try {
        const data = await api(characterStatusUrl());
        characterJobState = data.character_job || null;
        if (data.project_status) state.status = data.project_status;
        if (!characterJobState) {
          render();
          return;
        }
        if (characterJobState.status === "completed") {
          await fetchProject(false);
          characterPollingState = "completed";
          hideProcessingDialog();
          render();
          return;
        }
        if (characterJobState.status === "failed") {
          await fetchProject(false).catch(function () {});
          characterPollingState = "failed";
          hideProcessingDialog();
          render();
          return;
        }
        if (!["queued", "processing"].includes(characterJobState.status)) return;
        showProcessingDialog({ message: "キャラクター設定を生成しています…", progress: "サーバー上の処理状態を復元しています", submessage: "再読み込み前に開始したJobを引き続き確認します。" });
        await pollCharacterJob(characterJobState.id, "キャラクター設定を生成しています…");
      } catch (error) {
        if (characterRecoveryKind(error) === "not_found") await rediscoverCharacterStateAfterNotFound();
        else showCharacterRecoveryNotice(characterRecoveryKind(error));
      } finally {
        characterStateSyncInFlight = false;
        if (activeStep === "characters" && !characterPolling) render();
      }
    }

    function renderCharacters() {
      const characters = state.characters || [];
      const jobStatus = characterJobState?.status;
      const jobActive = jobStatus === "queued" || jobStatus === "processing"
        || (!characterJobState && state.status === "processing" && state.current_step === "characters");
      const busy = characterPolling || jobActive || characterStateSyncInFlight;
      const disabled = busy ? " disabled" : "";
      const next = busy
        ? '<div class="save-row"><button type="button" class="primary-button compact-button" data-next-step="storyboard" disabled>ネームを作る <span aria-hidden="true">→</span></button></div>'
        : nextButton("storyboard", "ネームを作る");
      const actionLabel = jobActive && characterPolling
        ? "人物設定を作成中…"
        : jobActive
          ? "処理状態を再確認"
          : jobStatus === "failed"
            ? "人物設定を再試行"
            : characters.length ? "人物設定を作り直す" : "生成する";
      const jobNotice = jobStatus === "failed"
        ? '<div class="form-notice error-notice" role="alert"><span class="notice-mark">!</span><p>' + escapeHtml(characterJobState.error || "人物設定の生成に失敗しました。再試行できます。") + '</p></div>'
        : jobActive
          ? '<div class="form-notice" role="status"><span class="notice-mark">…</span><p>人物設定を生成しています。再読み込み後もサーバーのJob状態から復元します。</p></div>'
          : "";
      const fields = function (character) {
        const textField = function (key, label, rows) { return '<label class="editor-label">' + escapeHtml(label) + '<textarea data-character-field="' + key + '" rows="' + rows + '">' + escapeHtml(character[key] || "") + '</textarea></label>'; };
        return textField("appearance", "外見", 3) + textField("clothing", "服装", 2) + textField("personality", "性格", 2) + textField("distinguishing_features", "識別ポイント", 2) + textField("visual_prompt", "生成用の一貫性メモ", 2) + textField("negative_constraints", "変えない制約", 2);
      };
      const cards = characters.map(function (character, index) {
        return '<article class="surface-panel character-card" data-character-id="' + escapeAttr(character.id) + '"><div class="character-card-header"><div><h3>' + escapeHtml(character.name || "名前未設定") + '</h3><p>' + escapeHtml(character.role || "役割未設定") + ' / ' + escapeHtml(character.age_range || "年齢未設定") + '</p></div><span class="character-stamp">' + String(index + 1).padStart(2, "0") + '</span></div><div class="character-fields">' + '<label class="editor-label">名前<input data-character-field="name" value="' + escapeAttr(character.name || "") + '"></label>' + '<label class="editor-label">役割<input data-character-field="role" value="' + escapeAttr(character.role || "") + '"></label>' + fields(character) + '</div></article>';
      }).join("");
      const body = characters.length
        ? '<div class="character-grid">' + cards + '</div><div class="save-row"><button type="button" class="secondary-button compact-button" data-regenerate-characters' + disabled + '>' + escapeHtml(actionLabel) + '</button><button type="button" class="primary-button compact-button" data-save-characters' + disabled + '>キャラクターを保存</button></div>' + next
        : '<section class="surface-panel empty-panel"><h3>キャラクターバイブルを作る</h3><p>解析結果から、同じ人物を描き続けるための基準を作成します。</p><button type="button" class="primary-button compact-button" data-generate-characters' + disabled + '>' + escapeHtml(actionLabel) + '</button></section>';
      content.innerHTML = heading("キャラクターを固定する", "同一人物の外見・服装を後続コマへ引き継ぐための設定です。") + renderCharacterRecoveryNotice() + jobNotice + body;
      content.querySelector("[data-generate-characters]")?.addEventListener("click", generateCharacters);
      content.querySelector("[data-regenerate-characters]")?.addEventListener("click", generateCharacters);
      content.querySelector("[data-character-recheck]")?.addEventListener("click", recheckCharacterGenerationState);
      content.querySelector("[data-character-reload]")?.addEventListener("click", function () { window.location.reload(); });
      content.querySelector("[data-save-characters]")?.addEventListener("click", function () {
        const next = characters.map(function (character) {
          const card = content.querySelector('[data-character-id="' + CSS.escape(character.id) + '"]');
          const result = { ...character };
          card?.querySelectorAll("[data-character-field]").forEach(function (input) { result[input.dataset.characterField] = input.value; });
          return result;
        });
        saveProject({ characters: next, current_step: "characters" }, "キャラクター設定を保存しました", true);
      });
      content.querySelector("[data-next-step]")?.addEventListener("click", function () { goToStep("storyboard"); });
    }

    async function generateCharacters() {
      if (!state.analysis) { showToast("先に物語解析を生成してください", "error"); return; }
      if (characterPolling || characterStateSyncInFlight || ["queued", "processing"].includes(characterJobState?.status)) return;
      const button = content.querySelector("[data-generate-characters], [data-regenerate-characters]");
      const originalButtonText = button?.textContent || "生成する";
      if (button) { button.disabled = true; button.textContent = "人物設定を作成中…"; }
      showProcessingDialog({ message: "キャラクター設定を生成しています…", progress: "人物の外見と関係性を整理しています", submessage: "後続のコマでも同じ人物として描ける設定を作成しています。" });
      let handedOff = false;
      try {
        const data = await api("/api/projects/" + encodeURIComponent(state.id) + "/characters", { method: "POST", body: "{}" });
        state = data.project;
        if (data.accepted && data.job?.id) {
          handedOff = true;
          characterJobState = data.job;
          characterRecoveryNotice = null;
          updateProcessingDialog({ progress: "生成キューへ登録しました", submessage: "サーバー側のJob状態を確認しながら人物設定を保存します。" });
          await pollCharacterJob(data.job.id, "キャラクター設定を生成しています…");
          return;
        }
        characterJobState = null;
        showToast("キャラクターバイブルを作成しました");
        render();
      } catch (error) {
        // POST応答だけが失われた場合は再送せず、既存Jobを一度だけ再確認する。
        try {
          const status = await api(characterStatusUrl(), { timeoutMs: 10000 });
          characterJobState = status.character_job || characterJobState;
          if (status.project_status) state.status = status.project_status;
          if (characterJobState && ["queued", "processing"].includes(characterJobState.status)) {
            handedOff = true;
            await pollCharacterJob(characterJobState.id, "キャラクター設定を生成しています…");
            return;
          }
          await fetchProject(false).catch(function () {});
        } catch (_statusError) {
          // 下の表示へ進み、状態を固定せず再確認導線を残す。
        }
        showToast(error.message, "error");
        if (button) { button.disabled = false; button.textContent = originalButtonText; }
        render();
      } finally {
        if (!handedOff) hideProcessingDialog();
        if (activeStep === "characters" && !characterPolling) render();
      }
    }

    function panelTemplate(pageId, panel, index) {
      const input = function (key, label, value, full) { return '<label class="editor-label ' + (full ? "full" : "") + '">' + escapeHtml(label) + '<textarea data-panel-field="' + key + '" rows="' + (full ? "3" : "2") + '">' + escapeHtml(Array.isArray(value) ? textAreaValue(value) : value || "") + '</textarea></label>'; };
      const scalar = function (key, label, value) { return '<label class="editor-label">' + escapeHtml(label) + '<input data-panel-field="' + key + '" value="' + escapeAttr(value || "") + '"></label>'; };
      const importanceOptions = [{value:"low", label:"小"}, {value:"medium", label:"中"}, {value:"high", label:"大"}, {value:"critical", label:"特大"}].map(function (option) { return '<option value="' + option.value + '"' + (option.value === (panel.importance || "medium") ? " selected" : "") + '>' + option.label + '</option>'; }).join("");
      const importance = '<label class="editor-label">重要度<select data-panel-field="importance">' + importanceOptions + '</select></label>';
      return '<article class="storyboard-panel" data-panel-id="' + escapeAttr(panel.id) + '"><span class="panel-index">' + String(panel.order || index + 1).padStart(2, "0") + '</span><div><div class="panel-editor-grid">' + input("description", "コマの意図", panel.description, true) + scalar("panel_role", "コマの役割", panel.panel_role) + importance + scalar("scene_type", "シーン種別", panel.scene_type) + scalar("shot_type", "カメラ", panel.shot_type) + scalar("action", "行動", panel.action) + scalar("expression", "表情", panel.expression) + scalar("background", "背景", panel.background) + scalar("characters", "登場人物（カンマ区切り）", (panel.characters || []).join(", ")) + input("dialogue", "セリフ（1行1つ）", panel.dialogue, false) + input("narration", "ナレーション", panel.narration, false) + input("sfx", "効果音", panel.sfx, false) + '</div><div class="panel-mini-actions"><button type="button" class="text-button" data-panel-move="up" data-page-id="' + escapeAttr(pageId) + '" data-panel-id="' + escapeAttr(panel.id) + '">↑ 上へ</button><button type="button" class="text-button" data-panel-move="down" data-page-id="' + escapeAttr(pageId) + '" data-panel-id="' + escapeAttr(panel.id) + '">↓ 下へ</button><button type="button" class="text-button danger-button" data-panel-delete data-page-id="' + escapeAttr(pageId) + '" data-panel-id="' + escapeAttr(panel.id) + '">削除</button><button type="button" class="primary-button compact-button" data-save-panel data-page-id="' + escapeAttr(pageId) + '" data-panel-id="' + escapeAttr(panel.id) + '">コマを保存</button></div></div></article>';
    }

    function renderStoryboard() {
      const pages = state.storyboard || [];
      const jobStatus = storyboardJobState?.status;
      const jobActive = jobStatus === "queued" || jobStatus === "processing";
      const actionLabel = storyboardPolling && jobActive
        ? "ネームを作成中…"
        : jobActive
          ? "処理状態を再確認"
          : jobStatus === "failed"
            ? "ネームを再試行"
            : pages.length
              ? "ネームを作り直す"
              : "ネームを生成する";
      const actionDisabled = storyboardPolling && jobActive ? " disabled" : "";
      const jobNotice = jobStatus === "failed"
        ? '<div class="form-notice error-notice" role="alert"><span class="notice-mark">!</span><p>' + escapeHtml(storyboardJobState.error || "Storyboardの生成に失敗しました。再試行できます。") + '</p></div>'
        : jobActive
          ? '<div class="form-notice" role="status"><span class="notice-mark">…</span><p>Storyboardを生成しています。再読み込み後もサーバーのJob状態から復元します。</p></div>'
          : "";
      const pageMarkup = pages.map(function (page, pageIndex) {
        const panels = (page.panels || []).map(function (panel, panelIndex) { return panelTemplate(page.id, panel, panelIndex); }).join("");
        const layoutOptions = [{value:"classic", label:"自動"}, {value:"drama", label:"標準ドラマ"}, {value:"conversation", label:"会話"}, {value:"action", label:"アクション"}, {value:"psychological", label:"心理"}, {value:"four_panel", label:"4コマ（均等）"}, {value:"hero", label:"1コマ"}, {value:"wide", label:"横長重視"}].map(function (option) { return '<option value="' + option.value + '"' + (option.value === (page.layout || "classic") ? " selected" : "") + '>' + option.label + '</option>'; }).join("");
        const resolvedTemplate = page.layout_geometry?.template || "未計算";
        return '<article class="page-card" data-page-id="' + escapeAttr(page.id) + '"><header class="page-card-header"><div class="page-card-title"><span class="page-number-badge">' + String(pageIndex + 1).padStart(2, "0") + '</span><div><h3>' + escapeHtml(page.title || "ページ") + '</h3><p>' + (page.panels || []).length + 'コマ / ' + escapeHtml(page.layout || "classic") + ' → ' + escapeHtml(resolvedTemplate) + '</p></div></div><div class="page-card-actions"><label class="page-layout-control">レイアウト<select data-page-layout data-page-id="' + escapeAttr(page.id) + '">' + layoutOptions + '</select></label><button type="button" class="text-button" data-repair-page-layout data-page-id="' + escapeAttr(page.id) + '">配置を再計算</button><button type="button" class="text-button" data-page-move="up" data-page-id="' + escapeAttr(page.id) + '">↑</button><button type="button" class="text-button" data-page-move="down" data-page-id="' + escapeAttr(page.id) + '">↓</button><button type="button" class="text-button danger-button" data-page-delete data-page-id="' + escapeAttr(page.id) + '">ページ削除</button></div></header><div class="panel-list">' + panels + '</div><div class="add-row"><button type="button" class="outline-button" data-add-panel data-page-id="' + escapeAttr(page.id) + '">＋ コマを追加</button></div></article>';
      }).join("");
      const body = pages.length ? jobNotice + '<div class="storyboard-list">' + pageMarkup + '</div><div class="save-row"><button type="button" class="outline-button" data-add-page>＋ ページを追加</button><button type="button" class="primary-button compact-button" data-generate-storyboard' + actionDisabled + '>' + actionLabel + '</button></div>' + nextButton("generate", "コマ生成へ") : jobNotice + '<section class="surface-panel empty-panel"><h3>ページとコマを設計する</h3><p>解析、設定、人物情報をもとに、読める流れを組み立てます。</p><button type="button" class="primary-button compact-button" data-generate-storyboard' + actionDisabled + '>' + actionLabel + '</button></section>';
      const orderNote = '<div class="reading-order-note"><strong>' + escapeHtml(languageLabel(state.settings)) + ' / ' + escapeHtml(readingDirectionLabel(state.settings)) + '</strong><span>Panel.orderは読者の論理読順です。Knowledgeの逆方向指定よりProject設定を優先します。</span></div>';
      content.innerHTML = heading("ネームを編集する", "ページをまたぐ展開と、コマごとの視線の流れを確認します。") + orderNote + body;
      bindStoryboardEvents();
    }

    function newPanel(order) {
      return { id: uuid("panel"), order: order, description: "追加したコマの意図を入力", panel_role: "", scene_type: "dialogue", importance: "medium", shot_type: "バストアップ", characters: [], action: "", expression: "", background: "", dialogue: [], narration: [], sfx: [], bubble_order: [], narration_order: [], sfx_order: [], generation_prompt: "", image_url: null, generation_status: "not_started", generation_error: null, revision: 0, crop_mode: "fit" };
    }

    function bindStoryboardEvents() {
      content.querySelector("[data-generate-storyboard]")?.addEventListener("click", generateStoryboard);
      content.querySelector("[data-next-step]")?.addEventListener("click", function () { goToStep("generate"); });
      content.querySelector("[data-add-page]")?.addEventListener("click", function () {
        const pages = [...(state.storyboard || [])];
        pages.push({ id: uuid("page"), page_number: pages.length + 1, title: "追加ページ", layout: "classic", panels: [newPanel(1)] });
        saveStoryboard(pages, "ページを追加しました");
      });
      content.querySelectorAll("[data-page-layout]").forEach(function (select) { select.addEventListener("change", function () {
        const pages = structuredClone(state.storyboard); const page = pages.find(function (item) { return item.id === select.dataset.pageId; }); if (!page) return; page.layout = select.value; saveStoryboard(pages, "ページレイアウトを更新しました");
      }); });
      content.querySelectorAll("[data-repair-page-layout]").forEach(function (button) { button.addEventListener("click", async function () {
        if (button.disabled) return;
        button.disabled = true;
        try {
          const data = await api("/api/projects/" + encodeURIComponent(state.id) + "/pages/" + encodeURIComponent(button.dataset.pageId) + "/layout/repair", { method: "POST", body: "{}" });
          state = data.project;
          showToast("このページのコマ割りと文字配置を再計算しました");
          render();
        } catch (error) { showToast(error.message, "error"); button.disabled = false; }
      }); });
      content.querySelectorAll("[data-page-move]").forEach(function (button) { button.addEventListener("click", function () {
        const pages = [...state.storyboard]; const index = pages.findIndex(function (page) { return page.id === button.dataset.pageId; }); const nextIndex = button.dataset.pageMove === "up" ? index - 1 : index + 1; if (index < 0 || nextIndex < 0 || nextIndex >= pages.length) return; [pages[index], pages[nextIndex]] = [pages[nextIndex], pages[index]]; pages.forEach(function (page, i) { page.page_number = i + 1; }); saveStoryboard(pages, "ページの順番を更新しました");
      }); });
      content.querySelectorAll("[data-page-delete]").forEach(function (button) { button.addEventListener("click", function () {
        if (!window.confirm("このページを削除しますか？この操作は保存されます。")) return; const pages = state.storyboard.filter(function (page) { return page.id !== button.dataset.pageId; }); pages.forEach(function (page, i) { page.page_number = i + 1; }); saveStoryboard(pages, "ページを削除しました");
      }); });
      content.querySelectorAll("[data-add-panel]").forEach(function (button) { button.addEventListener("click", function () {
        const pages = structuredClone(state.storyboard); const page = pages.find(function (item) { return item.id === button.dataset.pageId; }); if (!page) return; page.panels = page.panels || []; page.panels.push(newPanel(page.panels.length + 1)); saveStoryboard(pages, "コマを追加しました");
      }); });
      content.querySelectorAll("[data-panel-move]").forEach(function (button) { button.addEventListener("click", function () {
        const pages = structuredClone(state.storyboard); const page = pages.find(function (item) { return item.id === button.dataset.pageId; }); if (!page) return; const index = page.panels.findIndex(function (panel) { return panel.id === button.dataset.panelId; }); const nextIndex = button.dataset.panelMove === "up" ? index - 1 : index + 1; if (index < 0 || nextIndex < 0 || nextIndex >= page.panels.length) return; [page.panels[index], page.panels[nextIndex]] = [page.panels[nextIndex], page.panels[index]]; page.panels.forEach(function (panel, i) { panel.order = i + 1; }); saveStoryboard(pages, "コマの順番を更新しました");
      }); });
      content.querySelectorAll("[data-panel-delete]").forEach(function (button) { button.addEventListener("click", function () {
        if (!window.confirm("このコマを削除しますか？")) return; const pages = structuredClone(state.storyboard); const page = pages.find(function (item) { return item.id === button.dataset.pageId; }); if (!page) return; page.panels = page.panels.filter(function (panel) { return panel.id !== button.dataset.panelId; }); page.panels.forEach(function (panel, i) { panel.order = i + 1; }); saveStoryboard(pages, "コマを削除しました");
      }); });
      content.querySelectorAll("[data-save-panel]").forEach(function (button) { button.addEventListener("click", function () {
        const pages = structuredClone(state.storyboard); const page = pages.find(function (item) { return item.id === button.dataset.pageId; }); const panel = page?.panels?.find(function (item) { return item.id === button.dataset.panelId; }); const originalPage = state.storyboard.find(function (item) { return item.id === button.dataset.pageId; }); const originalPanel = originalPage?.panels?.find(function (item) { return item.id === button.dataset.panelId; }); const card = button.closest(".storyboard-panel"); if (!panel || !card) return; card.querySelectorAll("[data-panel-field]").forEach(function (input) { const key = input.dataset.panelField; if (["dialogue", "narration", "sfx"].includes(key)) panel[key] = listFromText(input.value); else if (key === "characters") panel[key] = input.value.split(",").map(function (name) { return name.trim(); }).filter(Boolean); else panel[key] = input.value; }); const visualKeys = ["description", "shot_type", "characters", "action", "expression", "background"]; const visualChanged = visualKeys.some(function (key) { return JSON.stringify(panel[key] || "") !== JSON.stringify(originalPanel?.[key] || ""); }); if (visualChanged) { panel.generation_prompt = ""; panel.generation_status = "not_started"; panel.generation_error = null; } saveStoryboard(pages, "コマを保存しました");
      }); });
    }

    function saveStoryboard(storyboard, message) {
      storyboard.forEach(function (page, index) { page.page_number = index + 1; page.panels = (page.panels || []).map(function (panel, panelIndex) { return { ...panel, order: panelIndex + 1, bubble_order: (panel.dialogue || []).map(function (_item, itemIndex) { return itemIndex + 1; }), narration_order: (panel.narration || []).map(function (_item, itemIndex) { return itemIndex + 1; }), sfx_order: (panel.sfx || []).map(function (_item, itemIndex) { return itemIndex + 1; }) }; }); });
      saveProject({ storyboard: storyboard, current_step: "storyboard" }, message, true);
    }

    async function pollStoryboardJob(jobId, operationMessage) {
      const runId = ++storyboardPollRun;
      const startedAt = Date.now();
      const maximumPollingMs = 16 * 60 * 1000;
      let consecutiveNetworkErrors = 0;
      storyboardPolling = true;
      if (activeStep === "storyboard") render();
      try {
        while (Date.now() - startedAt < maximumPollingMs) {
          const interval = Date.now() - startedAt < 60 * 1000 ? 2000 : 5000;
          await new Promise(function (resolve) { window.setTimeout(resolve, interval); });
          if (runId !== storyboardPollRun) return;
          let data;
          try {
            data = await api("/api/projects/" + encodeURIComponent(state.id) + "/generation/status");
            consecutiveNetworkErrors = 0;
          } catch (error) {
            consecutiveNetworkErrors += 1;
            if (consecutiveNetworkErrors >= 4) throw error;
            updateProcessingDialog({
              message: operationMessage,
              progress: "通信を再確認しています",
              submessage: "Jobはサーバー側で継続します。状態の取得を再試行しています。"
            });
            continue;
          }
          const job = data.storyboard_job || (data.jobs || []).find(function (item) { return item.id === jobId; });
          if (!job || job.id !== jobId) throw new Error("Storyboardの処理状態を取得できませんでした");
          storyboardJobState = job;
          if (data.project_status) state.status = data.project_status;
          if (job.status === "completed") {
            await fetchProject(false);
            showToast("ページとコマの構成を作成しました");
            return;
          }
          if (job.status === "failed") {
            await fetchProject(false).catch(function () {});
            throw new Error(job.error || "Storyboardの生成に失敗しました");
          }
          if (job.status !== "queued" && job.status !== "processing") {
            throw new Error("Storyboardの処理が予期しない状態で終了しました");
          }
          updateProcessingDialog({
            message: operationMessage,
            progress: job.status === "queued" ? "生成キューで順番を待っています" : "AIがページとコマの構成を作成しています",
            submessage: "完了後に生成結果を保存します。画面を閉じても処理は継続します。"
          });
        }
        throw new Error("Storyboardの処理状況を確認できる時間を超えました。処理状態を再確認してください");
      } finally {
        if (runId === storyboardPollRun) {
          storyboardPolling = false;
          if (activeStep === "storyboard") render();
        }
      }
    }

    async function restoreStoryboardJobState() {
      try {
        const data = await api("/api/projects/" + encodeURIComponent(state.id) + "/generation/status");
        storyboardJobState = data.storyboard_job || null;
        if (data.project_status) state.status = data.project_status;
        if (!storyboardJobState) return;
        if (storyboardJobState.status === "completed") {
          await fetchProject(false);
          render();
          return;
        }
        if (storyboardJobState.status === "failed") {
          await fetchProject(false).catch(function () {});
          render();
          return;
        }
        if (!["queued", "processing"].includes(storyboardJobState.status)) return;
        showProcessingDialog({
          message: "ストーリーボードを生成しています…",
          progress: "サーバー上の処理状態を復元しています",
          submessage: "再読み込み前に開始したJobを引き続き確認します。"
        });
        try {
          await pollStoryboardJob(storyboardJobState.id, "ストーリーボードを生成しています…");
        } catch (error) {
          showToast(error.message, "error");
        } finally {
          hideProcessingDialog();
        }
      } catch (_error) {
        // 状態確認だけが失敗しても編集画面を利用不能にはしない。
      }
    }

    async function generateStoryboard() {
      if (!state.analysis) { showToast("先に物語解析を生成してください", "error"); return; }
      if (storyboardPolling) return;
      const button = content.querySelector("[data-generate-storyboard]");
      if (button) { button.disabled = true; button.textContent = "ネームを作成中…"; }
      showProcessingDialog({
        message: "ストーリーボードを生成しています…",
        progress: "ページとコマの流れを設計しています",
        submessage: "場面転換、視線の流れ、ページめくりを整理しています。"
      });
      try {
        const data = await api("/api/projects/" + encodeURIComponent(state.id) + "/storyboard", { method: "POST", body: "{}" });
        state = data.project;
        if (data.accepted && data.job?.id) {
          storyboardJobState = data.job;
          updateProcessingDialog({
            message: "ストーリーボードを生成しています…",
            progress: "生成キューへ登録しました",
            submessage: "Render上のバックグラウンド処理でネームを作成しています。"
          });
          await pollStoryboardJob(data.job.id, "ストーリーボードを生成しています…");
          return;
        }
        storyboardJobState = null;
        showToast("ページとコマの構成を作成しました");
        render();
      } catch (error) {
        showToast(error.message, "error");
        try {
          const status = await api("/api/projects/" + encodeURIComponent(state.id) + "/generation/status");
          storyboardJobState = status.storyboard_job || storyboardJobState;
          if (status.project_status) state.status = status.project_status;
          await fetchProject(false).catch(function () {});
        } catch (_statusError) {}
      } finally {
        storyboardPolling = false;
        hideProcessingDialog();
        if (activeStep === "storyboard") render();
      }
    }

    function applyGenerationStatus(data) {
      if (!data) return;
      const byId = Object.fromEntries((data.panels || []).map(function (panel) { return [panel.id, panel]; }));
      (state.storyboard || []).forEach(function (page) {
        (page.panels || []).forEach(function (panel) {
          const update = byId[panel.id];
          if (!update) return;
          panel.generation_status = update.status;
          panel.generation_error = update.error;
          panel.image_url = update.image_url;
          panel.revision = update.revision;
          panel.generation_metadata = update.generation_metadata;
        });
      });
      if (data.project_status) state.status = data.project_status;
      panelGenerationState = data.panel_generation || null;
    }

    function panelProgress(data, targetIds) {
      const panels = Array.isArray(data?.panels) ? data.panels : [];
      const snapshot = data?.panel_generation || {};
      const tracked = targetIds && targetIds.size
        ? panels.filter(function (panel) { return targetIds.has(panel.id); })
        : snapshot.batch_panel_ids?.length
          ? panels.filter(function (panel) { return snapshot.batch_panel_ids.includes(panel.id); })
          : panels;
      const counts = {
        completed: tracked.filter(function (panel) { return panel.status === "completed"; }).length,
        generating: tracked.filter(function (panel) { return panel.status === "processing"; }).length,
        waiting: tracked.filter(function (panel) { return panel.status === "queued"; }).length,
        failed: tracked.filter(function (panel) { return panel.status === "failed"; }).length
      };
      const current = tracked.find(function (panel) { return panel.status === "processing"; }) || tracked.find(function (panel) { return panel.status === "queued"; });
      return {
        tracked: tracked,
        current: current,
        total: targetIds && targetIds.size ? targetIds.size : Number(snapshot.total || tracked.length),
        ...counts
      };
    }

    function panelDialogOptions(operationMessage, data, progress) {
      const snapshot = data?.panel_generation || {};
      const current = progress.current;
      const currentPage = snapshot.current_page || current?.page_number;
      const currentPanel = snapshot.current_panel || current?.order;
      let message = operationMessage || "漫画画像を生成しています…";
      let submessage = "生成済みのコマは保存されています。状態はサーバーへ保存されます。";
      if (progress.generating) {
        if (currentPage && currentPanel) message = "ページ" + currentPage + "・コマ" + currentPanel + "を生成しています…";
        else if (progress.generating > 1) message = progress.generating + "コマを生成しています…";
        submessage = "画像生成には数分かかる場合があります。生成済みのコマは保存されています。";
      } else if (progress.waiting) {
        message = "画像生成の順番を待っています";
        submessage = progress.waiting + "コマが待機中です。Jobはサーバー側で継続します。";
      }
      const parts = [progress.completed + " / " + progress.total + " 完了"];
      if (progress.generating) parts.push("生成中 " + progress.generating);
      if (progress.waiting) parts.push("待機 " + progress.waiting);
      if (progress.failed) parts.push("失敗 " + progress.failed);
      return { message: message, progress: parts.join(" ・ "), submessage: submessage, actionLabel: "", action: null };
    }

    function panelStatusUrl() {
      return "/api/projects/" + encodeURIComponent(state.id) + "/generation/status";
    }

    function clearPanelPollingTimer() {
      if (panelPollingTimer) window.clearTimeout(panelPollingTimer);
      panelPollingTimer = null;
      if (panelPollingWaitResolve) panelPollingWaitResolve(false);
      panelPollingWaitResolve = null;
    }

    function cancelPanelPolling() {
      panelPollingRun += 1;
      clearPanelPollingTimer();
      if (panelPollingAbortController) panelPollingAbortController.abort();
      panelPollingAbortController = null;
      polling = false;
      panelPollingState = "idle";
    }

    function waitForPanelPoll(delay, runId) {
      return new Promise(function (resolve) {
        clearPanelPollingTimer();
        panelPollingWaitResolve = resolve;
        panelPollingTimer = window.setTimeout(function () {
          panelPollingTimer = null;
          panelPollingWaitResolve = null;
          resolve(runId === panelPollingRun);
        }, delay);
      });
    }

    function panelRecoveryKind(error) {
      if (error?.status === 401 || error?.category === "auth") return "auth";
      if (error?.status === 403 || error?.category === "forbidden") return "forbidden";
      if (error?.status === 404 || error?.category === "not_found") return "not_found";
      if (error?.category === "timeout") return "timeout";
      return "connection";
    }

    function showPanelRecoveryNotice(kind) {
      const notices = {
        connection: {
          message: "処理状況を確認できません。",
          detail: "サーバーとの接続が安定しないため、Jobの状態を確認できません。Jobを停止したとは判断していません。"
        },
        timeout: {
          message: "処理状況の確認がタイムアウトしました。",
          detail: "画像生成には時間がかかる場合があります。保存済みの状態を再確認できます。"
        },
        not_found: {
          message: "生成Jobを見つけられませんでした。",
          detail: "Projectの状態を再取得してください。保存済みのコマは保持されています。"
        },
        auth: {
          message: "ログイン状態を確認できません。",
          detail: "再ログイン後に、生成状況を再確認してください。"
        },
        forbidden: {
          message: "Projectへのアクセス権を確認できません。",
          detail: "Projectの所有者アカウントで再ログインしてください。"
        }
      };
      const notice = notices[kind] || notices.connection;
      hideProcessingDialog();
      panelPollingState = kind === "auth" || kind === "forbidden" ? "connection_error" : "unknown_recoverable";
      panelGenerationState = panelGenerationState
        ? { ...panelGenerationState, active: false, status: "unknown" }
        : { active: false, status: "unknown", total: 0, completed: 0, generating: 0, waiting: 0, failed: 0 };
      panelRecoveryNotice = { kind: kind, message: notice.message, detail: notice.detail };
      if (activeStep === "generate") render();
      showToast(notice.message + " 状態を再確認できます。", "error");
    }

    function showPanelStatusUnknown(reason) {
      showPanelRecoveryNotice(reason === "not_found" ? "not_found" : reason === "timeout" ? "timeout" : "connection");
    }

    function renderPanelRecoveryNotice() {
      if (!panelRecoveryNotice) return "";
      const next = encodeURIComponent(window.location.pathname + window.location.search);
      const loginAction = panelRecoveryNotice.kind === "auth" || panelRecoveryNotice.kind === "forbidden"
        ? '<a class="secondary-button compact-button" data-panel-login href="/login?next=' + escapeAttr(next) + '">再ログイン</a>'
        : "";
      return '<div class="form-notice error-notice generation-recovery-notice" role="alert"><span class="notice-mark">!</span><div><strong>' + escapeHtml(panelRecoveryNotice.message) + '</strong><p>' + escapeHtml(panelRecoveryNotice.detail) + '</p><div class="generation-recovery-actions"><button type="button" class="secondary-button compact-button" data-panel-recheck' + (panelRecheckInFlight ? " disabled" : "") + '>状態を再確認</button><button type="button" class="text-button" data-panel-reload>再読み込み</button>' + loginAction + '</div></div></div>';
    }

    async function rediscoverPanelStateAfterNotFound() {
      try {
        // Job状態APIを再送し続けず、Project本体を一度だけ再取得して保存済みPanelを確認する。
        await fetchProject(false);
        const panels = allPanels().map(function (item) { return item.panel; });
        const activePanels = panels.filter(function (panel) { return ["queued", "processing"].includes(panel.generation_status); });
        if (!activePanels.length) {
          const failed = panels.filter(function (panel) { return panel.generation_status === "failed"; }).length;
          panelGenerationState = {
            active: false,
            status: failed ? "partially_failed" : "completed",
            total: panels.length,
            completed: panels.filter(function (panel) { return panel.generation_status === "completed"; }).length,
            generating: 0,
            waiting: 0,
            failed: failed
          };
          panelPollingState = failed ? "partially_failed" : "completed";
          panelRecoveryNotice = null;
          hideProcessingDialog();
          if (activeStep === "generate") render();
          showToast("保存済みのPanel状態を表示しました");
          return true;
        }
      } catch (_error) {
        // 再取得にも失敗した場合は、下の非ブロッキング警告へ進む。
      }
      showPanelStatusUnknown("not_found");
      return false;
    }

    async function recheckPanelGenerationState() {
      if (panelRecheckInFlight || activeStep !== "generate") return;
      panelRecheckInFlight = true;
      cancelPanelPolling();
      hideProcessingDialog();
      render();
      try {
        const data = await api(panelStatusUrl(), { timeoutMs: 10000 });
        applyGenerationStatus(data);
        await fetchProject(false).catch(function () {});
        const snapshot = data.panel_generation || {};
        panelRecoveryNotice = null;
        if (snapshot.active) {
          const targetIds = snapshot.batch_panel_ids || snapshot.active_panel_ids || [];
          const operationMessage = targetIds.length === 1 ? "コマを再生成しています…" : "漫画画像を生成しています…";
          render();
          showProcessingDialog({
            message: operationMessage,
            progress: "サーバー上の生成状態を復元しています",
            submessage: "確認できたJobの状態に戻して処理を追跡します。"
          });
          await pollGeneration(targetIds, operationMessage);
        } else {
          panelPollingState = "completed";
          render();
          showToast("保存済みの生成状態を確認しました");
        }
      } catch (error) {
        if (panelRecoveryKind(error) === "not_found") await rediscoverPanelStateAfterNotFound();
        else showPanelRecoveryNotice(panelRecoveryKind(error));
      } finally {
        panelRecheckInFlight = false;
        if (activeStep === "generate") render();
      }
    }

    async function restorePanelGenerationState() {
      if (activeStep !== "generate" || polling || panelStateSyncInFlight || panelRecheckInFlight) return;
      panelStateSyncInFlight = true;
      try {
        const data = await api(panelStatusUrl(), { timeoutMs: 10000 });
        applyGenerationStatus(data);
        panelRecoveryNotice = null;
        render();
        const snapshot = data.panel_generation || {};
        if (!snapshot.active) return;
        const targetIds = snapshot.batch_panel_ids || snapshot.active_panel_ids || [];
        const operationMessage = targetIds.length === 1 ? "コマを再生成しています…" : "漫画画像を生成しています…";
        showProcessingDialog({
          message: operationMessage,
          progress: "サーバー上の生成状態を復元しています",
          submessage: "再読み込み前に開始したJobを引き続き確認します。"
        });
        await pollGeneration(targetIds, operationMessage);
      } catch (error) {
        if (panelRecoveryKind(error) === "not_found") await rediscoverPanelStateAfterNotFound();
        else showPanelRecoveryNotice(panelRecoveryKind(error));
      } finally {
        panelStateSyncInFlight = false;
        if (activeStep === "generate" && !polling) render();
      }
    }

    function renderGenerationRows() {
      const panelBusy = Boolean(panelGenerationState?.active || polling);
      return allPanels().map(function (item) {
        const panel = item.panel;
        const status = panel.generation_status || "not_started";
        const disabled = panelBusy ? " disabled" : "";
        const action = status === "failed" ? '<button type="button" class="text-button" data-retry-panel="' + escapeAttr(panel.id) + '"' + disabled + '>再試行</button>' : status === "completed" ? '<button type="button" class="text-button" data-regenerate-panel="' + escapeAttr(panel.id) + '"' + disabled + '>再生成</button>' : "";
        const thumb = panel.image_url ? '<img src="' + escapeAttr(panel.image_url) + '" alt="">' : '<span>' + escapeHtml(String((item.page.page_number || 1) + " / " + (panel.order || (item.page.panels || []).indexOf(panel) + 1))) + '</span>';
        const referenceLabel = (panel.knowledge_refs || []).map(function (ref) { return (ref.title || "Knowledge") + " v" + (ref.version_number || "?"); }).join(", ");
        const generationModel = panel.generation_metadata?.actual_model ? " / 実行モデル: " + escapeHtml(panel.generation_metadata.actual_model) + (panel.generation_metadata.fallback ? "（Astraからフォールバック）" : "") : "";
        const geometry = item.page.composition?.panels?.find(function (entry) { return String(entry.panel_id) === String(panel.id); }) || panel.geometry || {};
        const geometryLabel = geometry.shape ? " / " + escapeHtml(geometry.shape) + " / " + (Number(geometry.width || 0) / Math.max(Number(geometry.height || 1), .01)).toFixed(2) + ":1" : "";
        const metadata = 'ページ ' + escapeHtml(item.page.page_number) + ' / ' + escapeHtml(panel.shot_type || "ショット未設定") + geometryLabel + ' / Revision ' + escapeHtml(panel.revision || 0) + generationModel + (referenceLabel ? " / " + escapeHtml(referenceLabel) : "") + (panel.generation_error ? " / " + escapeHtml(panel.generation_error) : "");
        return '<div class="generation-panel-row"><div class="generation-thumb">' + thumb + '</div><div><strong>' + escapeHtml(panel.description || "コマの説明") + '</strong><small>' + metadata + '</small></div><span class="panel-status panel-status-' + escapeAttr(status) + '">' + escapeHtml(panelStatusLabels[status] || status) + '</span>' + action + '</div>';
      }).join("");
    }

    function renderGenerate() {
      const panels = allPanels();
      const generated = panels.filter(function (item) { return item.panel.generation_status === "completed"; }).length;
      const failed = panels.filter(function (item) { return item.panel.generation_status === "failed"; }).length;
      const snapshot = panelGenerationState || {};
      const panelBusy = Boolean(snapshot.active || polling || panelRecheckInFlight);
      const aggregate = snapshot.active && snapshot.total ? snapshot : { total: panels.length, completed: generated, generating: 0, waiting: 0, failed: failed };
      const globalStatus = snapshot.active ? '<div class="generation-global-status" role="status" aria-live="polite"><strong>' + aggregate.completed + ' / ' + aggregate.total + ' 完了</strong><span>' + (aggregate.generating ? '生成中 ' + aggregate.generating + ' ・ ' : '') + (aggregate.waiting ? '待機 ' + aggregate.waiting + ' ・ ' : '') + (aggregate.failed ? '失敗 ' + aggregate.failed : '処理を継続しています') + '</span>' + (snapshot.current_page && snapshot.current_panel ? '<span>現在：ページ' + escapeHtml(snapshot.current_page) + '・コマ' + escapeHtml(snapshot.current_panel) + '</span>' : '') + '</div>' : '';
      const disabled = panelBusy ? " disabled" : "";
      const layoutPreview = panels.length && state.storyboard?.[0]?.composition ? '<section class="surface-panel generation-layout-preview"><div class="generation-toolbar"><div><strong>画像生成前のページ配置</strong><p>台形・面積差・Breakout候補を先に確認できます。</p></div></div>' + pageStage(state.storyboard[0]) + '</section>' : '';
      content.innerHTML = heading("コマを生成する", "必要なコマだけを選び、生成後も一枚ずつ再生成できます。") + (panels.length ? '<div class="generate-rail"><section class="surface-panel panel-padding">' + renderPanelRecoveryNotice() + '<div class="generation-toolbar"><p>' + panels.length + 'コマ中 ' + generated + 'コマを生成済み</p><div class="generation-actions"><button type="button" class="secondary-button compact-button" data-retry-failed' + (failed && !panelBusy ? "" : " disabled") + '>失敗したコマを再試行</button><button type="button" class="primary-button compact-button" data-generate-all' + disabled + '>未生成をまとめて生成</button></div></div>' + globalStatus + '<div class="panel-status-list">' + renderGenerationRows() + '</div></section><aside class="generation-summary">' + layoutPreview + '<div class="surface-panel"><h3>今回の対象</h3><div class="generation-summary-number">' + panels.length + '</div><p>コマ。デモモードではすぐに確認できます。</p></div><div class="surface-panel"><h3>生成ルール</h3><p class="cost-note">キャラクター設定を毎回参照し、セリフは画像に描かずアプリ側で合成します。</p></div></aside></div>' + nextButton("edit", "編集画面へ") : '<section class="surface-panel empty-panel"><h3>先にネームを作成してください</h3><p>ページ・コマ構成ができると、必要な画像だけ生成できます。</p><button type="button" class="primary-button compact-button" data-goto-storyboard>ネームへ戻る</button></section>');
      content.querySelector("[data-generate-all]")?.addEventListener("click", function () { queueGeneration([], false, false); });
      content.querySelector("[data-retry-failed]")?.addEventListener("click", function () { queueGeneration([], true, false); });
      content.querySelectorAll("[data-retry-panel]").forEach(function (button) { button.addEventListener("click", function () { queueGeneration([button.dataset.retryPanel], true, true); }); });
      content.querySelectorAll("[data-regenerate-panel]").forEach(function (button) { button.addEventListener("click", function () { queueGeneration([button.dataset.regeneratePanel], false, true); }); });
      content.querySelector("[data-panel-recheck]")?.addEventListener("click", function () { recheckPanelGenerationState(); });
      content.querySelector("[data-panel-reload]")?.addEventListener("click", function () { window.location.reload(); });
      content.querySelector("[data-goto-storyboard]")?.addEventListener("click", function () { goToStep("storyboard"); });
      content.querySelector("[data-next-step]")?.addEventListener("click", function () { goToStep("edit"); });
    }

    async function queueGeneration(panelIds, retryFailed, force) {
      if (polling || panelGenerationState?.active || panelRecheckInFlight || panelStateSyncInFlight) return;
      const buttons = content.querySelectorAll("[data-generate-all], [data-retry-failed], [data-retry-panel], [data-regenerate-panel]");
      buttons.forEach(function (button) { button.disabled = true; });
      const operationMessage = force && panelIds.length === 1 ? "漫画画像を再生成しています…" : "漫画画像を生成しています…";
      panelRecoveryNotice = null;
      showProcessingDialog({
        message: operationMessage,
        progress: "生成対象を確認しています",
        submessage: "選択したコマだけを処理し、完了した画像から保存します。"
      });
      let phase = "preflight";
      try {
        // 状態不明後に古いJobが残っていても、POST前に再確認して二重生成を防ぐ。
        const currentStatus = await api(panelStatusUrl(), { timeoutMs: 10000 });
        applyGenerationStatus(currentStatus);
        if (currentStatus.panel_generation?.active) {
          const activeIds = currentStatus.panel_generation.batch_panel_ids || currentStatus.panel_generation.active_panel_ids || panelIds;
          panelRecoveryNotice = null;
          await pollGeneration(activeIds, operationMessage);
          return;
        }
        phase = "post";
        const data = await api("/api/projects/" + encodeURIComponent(state.id) + "/generate", { method: "POST", body: JSON.stringify({ panel_ids: panelIds, retry_failed: retryFailed, force: force }) });
        const queuedPanelIds = Array.isArray(data.queued_panel_ids) ? data.queued_panel_ids.filter(Boolean) : [];
        if (!queuedPanelIds.length) {
          const status = await api(panelStatusUrl(), { timeoutMs: 10000 });
          applyGenerationStatus(status);
          if (status.panel_generation?.active) {
            const activeIds = status.panel_generation.batch_panel_ids || status.panel_generation.active_panel_ids || [];
            await pollGeneration(activeIds, operationMessage);
          } else {
            hideProcessingDialog();
            showToast("生成対象はありません。完了済みのコマは再利用されます");
            await fetchProject(true);
          }
          return;
        }
        showToast(queuedPanelIds.length + "コマを生成キューに追加しました");
        updateProcessingDialog({ progress: queuedPanelIds.length + "コマを順番に生成します" });
        const initialStatus = await api(panelStatusUrl(), { timeoutMs: 10000 });
        applyGenerationStatus(initialStatus);
        await fetchProject(true);
        await pollGeneration(queuedPanelIds, operationMessage);
      } catch (error) {
        if (phase === "preflight") {
          showPanelRecoveryNotice(panelRecoveryKind(error));
          return;
        }
        // POST応答だけが失われた場合、再送せずserver stateを確認する。
        try {
          const status = await api(panelStatusUrl(), { timeoutMs: 10000 });
          applyGenerationStatus(status);
          if (status.panel_generation?.active) {
            const activeIds = status.panel_generation.batch_panel_ids || status.panel_generation.active_panel_ids || panelIds;
            await pollGeneration(activeIds, operationMessage);
            return;
          }
        } catch (_statusError) {}
        if (["auth", "forbidden", "not_found"].includes(panelRecoveryKind(error))) {
          showPanelRecoveryNotice(panelRecoveryKind(error));
        } else {
          hideProcessingDialog();
          showToast(error.message || "コマ生成を開始できませんでした", "error");
        }
        render();
      }
    }

    async function pollGeneration(targetPanelIds, operationMessage) {
      if (polling) return;
      const runId = ++panelPollingRun;
      polling = true;
      panelPollingState = "active";
      let targetIds = new Set(targetPanelIds || []);
      let finished = false;
      let recoveryShown = false;
      const startedAt = Date.now();
      const maximumPollingMs = 30 * 60 * 1000;
      let consecutiveNetworkErrors = 0;
      try {
        while (Date.now() - startedAt < maximumPollingMs) {
          const interval = Date.now() - startedAt < 60 * 1000 ? 1500 : 5000;
          if (!await waitForPanelPoll(interval, runId) || runId !== panelPollingRun) return;
          let data = null;
          const requestController = typeof AbortController === "function" ? new AbortController() : null;
          panelPollingAbortController = requestController;
          try {
            data = await api(panelStatusUrl(), { timeoutMs: 10000, signal: requestController?.signal });
            consecutiveNetworkErrors = 0;
          } catch (error) {
            if (runId !== panelPollingRun) return;
            const kind = panelRecoveryKind(error);
            if (kind === "auth" || kind === "forbidden") {
              panelPollingState = "connection_error";
              showPanelRecoveryNotice(kind);
              recoveryShown = true;
              break;
            }
            if (kind === "not_found") {
              await rediscoverPanelStateAfterNotFound();
              recoveryShown = true;
              break;
            }
            consecutiveNetworkErrors += 1;
            panelPollingState = "reconnecting";
            if (consecutiveNetworkErrors >= 4) {
              showPanelStatusUnknown(kind === "timeout" ? "timeout" : "connection");
              recoveryShown = true;
              break;
            }
            updateProcessingDialog({
              message: operationMessage || "漫画画像を生成しています…",
              progress: "進行状況を確認しています…",
              submessage: "一時的な通信エラーです。Jobはサーバー側で継続します。",
              actionLabel: "",
              action: null
            });
            continue;
          } finally {
            if (panelPollingAbortController === requestController) panelPollingAbortController = null;
          }
          if (runId !== panelPollingRun) return;
          applyGenerationStatus(data);
          const snapshot = data.panel_generation || {};
          const serverTargetIds = snapshot.batch_panel_ids || snapshot.active_panel_ids || [];
          if (snapshot.active && serverTargetIds.length && (!targetIds.size || !serverTargetIds.some(function (id) { return targetIds.has(id); }))) {
            // 古い対象Panelの応答が遅れても、サーバーが現在追跡しているbatchへ切り替える。
            targetIds = new Set(serverTargetIds);
          }
          const progress = panelProgress(data, targetIds);
          const hasTrackedActive = progress.tracked.some(function (panel) { return panel.status === "queued" || panel.status === "processing"; });
          if (!snapshot.active && hasTrackedActive) {
            // Jobが消えたのにPanelだけがactiveなら、サーバーのstale/orphan復旧を待つため永久pollしない。
            showPanelStatusUnknown("not_found");
            recoveryShown = true;
            break;
          }
          const active = Boolean(snapshot.active) || hasTrackedActive;
          if (targetIds.size && progress.tracked.length < targetIds.size && !snapshot.active) {
            showPanelStatusUnknown("not_found");
            recoveryShown = true;
            break;
          }
          if (!active) {
            finished = true;
            panelPollingState = progress.failed ? "partially_failed" : "completed";
            await fetchProject(false).catch(function () {});
            render();
            if (progress.failed) {
              showToast(progress.failed + "コマの生成に失敗しました。再試行できます", "error");
            } else {
              showToast("漫画画像の生成が完了しました");
            }
            break;
          }
          panelPollingState = progress.waiting && !progress.generating ? "waiting" : "generating";
          updateProcessingDialog(panelDialogOptions(operationMessage, data, progress));
          if (activeStep === "generate") render();
        }
        if (!finished && !recoveryShown && runId === panelPollingRun) {
          showPanelStatusUnknown("timeout");
          recoveryShown = true;
        }
      } catch (error) {
        if (runId === panelPollingRun && !recoveryShown) {
          showPanelRecoveryNotice(panelRecoveryKind(error));
          recoveryShown = true;
        }
      } finally {
        if (runId === panelPollingRun) {
          polling = false;
          panelPollingAbortController = null;
          clearPanelPollingTimer();
          if (finished) hideProcessingDialog();
          if (activeStep === "generate") render();
        }
      }
    }

    function pageStage(page) {
      if (!page) return '<div class="preview-frame-wrap"><p>ページがありません</p></div>';
      const panels = page.panels || [];
      const geometryMap = pageGeometryMap(page);
      const template = page.layout_geometry?.template || page.layout || "classic";
      const composition = page.composition && Number(page.composition.composition_version) >= 2 ? page.composition : null;
      const compositionMap = composition ? new Map((composition.panels || []).map(function (item) { return [String(item.panel_id), item]; })) : null;
      const movedItems = new Set((composition?.moved_text_items || []).map(function (item) { return String(item.panel_id) + "::" + String(item.item_id); }));
      function polygonStyle(points, geometry) {
        if (!Array.isArray(points) || points.length < 3) return "";
        const gx = Number(geometry?.x || 0), gy = Number(geometry?.y || 0), gw = Math.max(Number(geometry?.width || 1), .001), gh = Math.max(Number(geometry?.height || 1), .001);
        return "clip-path:polygon(" + points.map(function (point) { return percentValue((Number(point?.[0] || 0) - gx) / gw, 0) + "% " + percentValue((Number(point?.[1] || 0) - gy) / gh, 0) + "%"; }).join(",") + ");";
      }
      function overlayMarkup(item) {
        const itemType = item.type === "narration" ? "narration" : item.type === "sfx" ? "sfx" : item.type === "title" ? "title" : "bubble";
        const className = itemType === "bubble" ? "speech-bubble page-overlay-bubble" : itemType === "narration" ? "page-narration page-overlay-narration" : itemType === "title" ? "page-overlay-title" : "page-sfx page-overlay-sfx";
        const dataOrder = itemType === "bubble" ? "data-bubble-order" : itemType === "narration" ? "data-narration-order" : itemType === "sfx" ? "data-sfx-order" : "";
        const style = "left:" + percentValue(item.x, .04) + "%;top:" + percentValue(item.y, .04) + "%;width:" + percentValue(item.width, .2) + "%;height:" + percentValue(item.height, .1) + "%;z-index:" + escapeAttr(item.z_index || 4) + ";transform:rotate(" + Number(item.rotation || 0) + "deg);--text-scale:" + Math.max(.72, Math.min(1.25, Number(item.font_scale) || 1)) + ";";
        return '<span class="' + className + '" style="' + style + '" ' + dataOrder + '="' + escapeAttr(item.reading_priority || item.order || 1) + '">' + escapeHtml(item.text || "").replace(/\n/g, "<br>") + '</span>';
      }
      const panelHtml = panels.map(function (panel, index) {
        const geometry = compositionMap?.get(String(panel.id)) || geometryMap.get(String(panel.id)) || panel.geometry || {};
        const placement = ' style="left:' + percentValue(geometry.x, .025) + '%;top:' + percentValue(geometry.y, .02) + '%;width:' + percentValue(geometry.width, .95) + '%;height:' + percentValue(geometry.height, .9) + '%;' + (composition ? polygonStyle(geometry.polygon_points, geometry) : "") + 'z-index:' + escapeAttr(geometry.z_index || 1) + ';"';
        const imageClass = composition ? "panel-image-fill" : panel.crop_mode === "fill" ? "panel-image-fill" : "panel-image-fit";
        const logicalOrder = panel.order || index + 1;
        const objectPosition = composition ? ' style="object-position:' + escapeAttr(geometry.crop_anchor_x || "center") + ' ' + escapeAttr(geometry.crop_anchor_y || "center") + ';"' : "";
        const image = panel.image_url ? '<img class="' + imageClass + '"' + objectPosition + ' src="' + escapeAttr(panel.image_url) + '" alt="ページ' + escapeAttr(page.page_number) + ' コマ' + escapeAttr(logicalOrder) + '">' : '<div class="manga-panel-placeholder">ARTWORK<br>未生成</div>';
        const textMarkup = panelTextLayout(panel).map(function (item) {
          if (composition && movedItems.has(String(panel.id) + "::" + String(item.id || ""))) return "";
          const itemType = item.type === "narration" ? "narration" : item.type === "sfx" ? "sfx" : "bubble";
          const className = itemType === "bubble" ? "speech-bubble bubble-side-" + (item.side || bubbleSide((item.order || 1) - 1, state.settings)) : itemType === "narration" ? "page-narration" : "page-sfx";
          const dataOrder = itemType === "bubble" ? "data-bubble-order" : itemType === "narration" ? "data-narration-order" : "data-sfx-order";
          const style = 'left:' + percentValue(item.x, .06) + '%;top:' + percentValue(item.y, .06) + '%;width:' + percentValue(item.width, .4) + '%;height:' + percentValue(item.height, .14) + '%;--text-scale:' + Math.max(.72, Math.min(1, Number(item.font_scale) || 1)) + ';';
          return '<span class="' + className + '" style="' + style + '" ' + dataOrder + '="' + escapeAttr(item.order || 1) + '">' + escapeHtml(item.text || "").replace(/\n/g, "<br>") + '</span>';
        }).join("");
        return '<div class="manga-panel' + (composition ? ' composition-panel shape-' + escapeAttr(geometry.shape || "rectangle") : "") + '" role="group" aria-label="コマ' + escapeAttr(logicalOrder) + '" data-reading-order="' + escapeAttr(logicalOrder) + '" data-visual-column="' + escapeAttr(geometry.column || panel.visual_position?.column || 1) + '" data-visual-row="' + escapeAttr(geometry.row || panel.visual_position?.row || index + 1) + '" data-panel-area="' + escapeAttr(geometry.area || "") + '"' + placement + '>' + image + textMarkup + '</div>';
      }).join("");
      const breakoutHtml = composition ? (composition.breakouts || []).filter(function (item) { return item && item.enabled !== false; }).map(function (item) {
        const source = panels.find(function (panel) { return String(panel.id) === String(item.source_panel_id || item.panel_id); });
        if (!source?.image_url) return "";
        const style = 'left:' + percentValue(item.x, .04) + '%;top:' + percentValue(item.y, .04) + '%;width:' + percentValue(item.width, .22) + '%;height:' + percentValue(item.height, .34) + '%;z-index:' + escapeAttr(item.z_index || 3) + ';';
        return '<div class="manga-breakout manga-breakout-' + escapeAttr(item.type || "character") + '" style="' + style + '"><img src="' + escapeAttr(source.image_url) + '" alt="前景ブレイクアウト"></div>';
      }).join("") : "";
      const overlayHtml = composition ? (composition.overlays || []).map(overlayMarkup).join("") : "";
      const direction = htmlDirection(state.settings);
      return '<div class="preview-frame-wrap" data-language="' + escapeAttr(canonicalLanguage(state.settings)) + '"><div class="manga-page layout-' + escapeAttr(template) + (composition ? ' composition-v2' : '') + '" dir="' + direction + '" data-layout-version="' + escapeAttr(page.layout_version || 1) + '" data-composition-version="' + escapeAttr(composition?.composition_version || 1) + '" data-reading-direction="' + escapeAttr(readingDirectionKey(state.settings)) + '">' + panelHtml + breakoutHtml + overlayHtml + '<span class="manga-page-number" aria-label="ページ番号 ' + escapeAttr(page.page_number || "") + '">' + escapeHtml(page.page_number || "") + '</span></div></div>';
    }

    function renderEdit() {
      const entries = allPanels();
      if (!selectedPanelId || !entries.some(function (item) { return item.panel.id === selectedPanelId; })) selectedPanelId = entries[0]?.panel.id || null;
      const selected = entries.find(function (item) { return item.panel.id === selectedPanelId; });
      const panel = selected?.panel;
      const list = entries.map(function (item) {
        const active = item.panel.id === selectedPanelId;
        const image = item.panel.image_url ? '<img src="' + escapeAttr(item.panel.image_url) + '" alt="">' : '<span class="edit-thumb-placeholder"></span>';
        return '<button type="button" class="edit-panel-button' + (active ? " active" : "") + '" data-edit-panel="' + escapeAttr(item.panel.id) + '">' + image + '<span><strong>P' + escapeHtml(item.page.page_number) + ' / コマ' + escapeHtml(item.panel.order || (item.page.panels || []).indexOf(item.panel) + 1) + ' / ' + escapeHtml(item.panel.description || "コマ") + '</strong><small>' + escapeHtml(panelStatusLabels[item.panel.generation_status || "not_started"]) + '</small></span><span aria-hidden="true">→</span></button>';
      }).join("");
      const form = panel ? '<form class="edit-form" id="panel-edit-form"><h3>コマの仕上げ</h3><label class="editor-label">生成プロンプト<textarea class="editor-textarea" data-edit-field="generation_prompt" rows="7">' + escapeHtml(panel.generation_prompt || "") + '</textarea><small>画像に文字は描かず、アプリ側でセリフを載せます。</small></label><label class="editor-label">セリフ<textarea class="editor-textarea" data-edit-field="dialogue" rows="4">' + escapeHtml(textAreaValue(panel.dialogue)) + '</textarea></label><label class="editor-label">ナレーション<textarea class="editor-textarea" data-edit-field="narration" rows="3">' + escapeHtml(textAreaValue(panel.narration)) + '</textarea></label><label class="editor-label">表示方法<select data-edit-field="crop_mode"><option value="fit"' + (panel.crop_mode === "fit" ? " selected" : "") + '>全面表示（cover crop）</option><option value="fill"' + (panel.crop_mode === "fill" ? " selected" : "") + '>枠に合わせる（cover crop）</option></select></label><div class="save-row"><button type="submit" class="primary-button compact-button">変更を保存</button><button type="button" class="secondary-button compact-button" data-regenerate-selected>このコマを再生成</button></div></form>' : '<div class="empty-panel"><h3>編集するコマを選んでください</h3></div>';
      content.innerHTML = heading("ページを仕上げる", "画像、セリフ、ナレーションをコマごとに確認します。") + (entries.length ? '<div class="editor-workspace"><section class="surface-panel edit-panel-list">' + list + '</section><section class="surface-panel">' + pageStage(selected?.page) + form + '</section></div>' + nextButton("qa", "Knowledge-aware QAへ") : '<section class="surface-panel empty-panel"><h3>生成済みのコマがありません</h3><p>コマ生成画面から必要な画像を作成してください。</p><button type="button" class="primary-button compact-button" data-goto-generate>コマ生成へ</button></section>');
      content.querySelectorAll("[data-edit-panel]").forEach(function (button) { button.addEventListener("click", function () { selectedPanelId = button.dataset.editPanel; render(); }); });
      content.querySelector("#panel-edit-form")?.addEventListener("submit", function (event) { event.preventDefault(); savePanelEdit(panel.id); });
      content.querySelector("[data-regenerate-selected]")?.addEventListener("click", function () { queueGeneration([panel.id], false, true); });
      content.querySelector("[data-goto-generate]")?.addEventListener("click", function () { goToStep("generate"); });
      content.querySelector("[data-next-step]")?.addEventListener("click", function () { goToStep("qa"); });
    }

    async function savePanelEdit(panelId) {
      const payload = {};
      content.querySelectorAll("[data-edit-field]").forEach(function (input) { const key = input.dataset.editField; payload[key] = ["dialogue", "narration"].includes(key) ? listFromText(input.value) : input.value; });
      try {
        const data = await api("/api/projects/" + encodeURIComponent(state.id) + "/panels/" + encodeURIComponent(panelId), { method: "PATCH", body: JSON.stringify(payload) });
        state = data.project;
        showToast("コマの内容を保存しました");
        render();
      } catch (error) { showToast(error.message, "error"); }
    }

    function renderQA() {
      const result = state.quality_check;
      const statusLabel = result?.status === "pass" ? "確認済み" : result ? "要確認" : "未確認";
      const checks = (result?.checks || []).map(function (check) {
        const checkStatus = check.status === "pass" ? "pass" : check.status === "error" ? "error" : "warning";
        const checkText = check.status === "pass" ? "OK" : check.status === "error" ? "要修正" : "確認";
        return '<li class="qa-check"><span class="qa-check-status qa-check-status-' + checkStatus + '">' + checkText + '</span><div><strong>' + escapeHtml(check.label) + '</strong><p>' + escapeHtml(check.detail) + '</p></div></li>';
      }).join("");
      const issues = (result?.issues || []).map(function (issue) { return '<li class="qa-issue"><strong>' + escapeHtml(issue.label) + '</strong><span>' + escapeHtml(issue.detail) + '</span></li>'; }).join("");
      const warnings = (result?.warnings || []).map(function (warning) { return '<li class="qa-warning"><strong>' + escapeHtml(warning.label) + '</strong><span>' + escapeHtml(warning.detail) + '</span></li>'; }).join("");
      const referenceItems = result?.knowledge_refs || [];
      const refs = referenceItems.map(function (ref) { return '<li><strong>' + escapeHtml(ref.title) + '</strong><span>Version ' + escapeHtml(ref.version_number) + ' / ' + escapeHtml((ref.chunk_ids || []).length) + ' Chunk</span></li>'; }).join("");
      const resolutionStatus = result?.knowledge_resolution_status;
      const noReferenceMessages = {
        no_selection: ["Knowledge未選択", "このProjectにはKnowledgeが設定されていません。"],
        selected_no_relevant_chunks: ["該当Chunkなし", "Knowledgeは選択されていますが、品質確認Scopeに該当するChunkがありません。"],
        processing_not_ready: ["Knowledge処理中", "選択したKnowledgeの処理完了後にもう一度実行してください。"],
        disabled: ["Knowledge無効", "Project設定またはDocument設定でKnowledgeが無効です。"],
      };
      const noReference = noReferenceMessages[resolutionStatus] || ["QA未実行", "品質確認を実行すると、参照したKnowledgeを表示します。"];
      const knowledgeSummary = '<section class="surface-panel panel-padding qa-knowledge-summary"><div><p class="eyebrow">KNOWLEDGE</p><h3>使用中: ' + referenceItems.length + '件</h3></div>' + (refs ? '<details><summary>参照したKnowledgeを確認</summary><ul>' + refs + '</ul></details>' : '<span class="knowledge-resolution-label">' + escapeHtml(noReference[0]) + '</span>') + '</section>';
      const resultMarkup = result ? '<section class="surface-panel panel-padding qa-result"><div class="qa-result-header"><div><p class="eyebrow">QUALITY REPORT</p><h3>制作状態: ' + escapeHtml(statusLabel) + '</h3><p>確認日時: ' + escapeHtml(result.checked_at || "") + '</p></div><span class="qa-result-badge qa-result-' + escapeAttr(result.status || "attention") + '">' + escapeHtml(statusLabel) + '</span></div><ul class="qa-check-list">' + checks + '</ul>' + (issues ? '<div class="qa-issues"><h4>修正が必要な項目</h4><ul>' + issues + '</ul></div>' : "") + (warnings ? '<div class="qa-issues"><h4>確認してください</h4><ul>' + warnings + '</ul></div>' : "") + (refs ? '<div class="knowledge-reference-list"><h4>今回参照したKnowledge</h4><ul>' + refs + '</ul></div>' : '<div class="knowledge-workspace-note"><strong>' + escapeHtml(noReference[0]) + '</strong><span>' + escapeHtml(noReference[1]) + '</span></div>') + '</section>' : '<section class="surface-panel empty-panel"><h3>書き出し前に品質を確認する</h3><p>本文、ネーム、画像状態、ページ内のコマ数とKnowledgeの解決状況を確認します。判定は決定的なチェックを中心に行います。</p></section>';
      const noSelection = (state.knowledge || []).length === 0;
      const warningMarkup = qaKnowledgeWarningOpen && noSelection ? '<section class="surface-panel panel-padding qa-knowledge-warning" role="alert"><h3>このProjectにはKnowledgeが設定されていません。</h3><p>共通Knowledgeを適用してQAを実行しますか？</p><div class="save-row"><button type="button" class="primary-button compact-button" data-apply-recommended-knowledge>推奨Knowledgeを適用</button><button type="button" class="secondary-button compact-button" data-open-knowledge-settings>Knowledge設定を開く</button><button type="button" class="text-button" data-continue-without-knowledge>Knowledgeなしで続行</button></div></section>' : '';
      const recommendationNotice = noSelection && !qaKnowledgeWarningOpen ? '<div class="knowledge-workspace-note"><strong>推奨Knowledgeを確認できます</strong><span>品質確認の前に適用できます。既存作品へ自動では追加しません。</span></div>' : '';
      content.innerHTML = heading("Knowledge-aware QA", "書き出し前に制作状態と、どのKnowledge Versionを参照したかを確認します。") + knowledgeSummary + warningMarkup + recommendationNotice + '<div class="qa-toolbar"><div class="knowledge-workspace-note"><strong>確認対象</strong><span>原作の意図を変える判定ではなく、編集を続けるための状態チェックです。</span></div><button type="button" class="primary-button compact-button" data-run-quality-check>品質を確認する</button></div>' + resultMarkup + nextButton("preview", "Previewを見る");
      content.querySelector("[data-run-quality-check]")?.addEventListener("click", function () { runQualityCheck(false); });
      content.querySelector("[data-open-knowledge-settings]")?.addEventListener("click", function () { qaKnowledgeWarningOpen = false; goToStep("knowledge"); });
      content.querySelector("[data-continue-without-knowledge]")?.addEventListener("click", function () { qaKnowledgeWarningOpen = false; runQualityCheck(true); });
      content.querySelector("[data-apply-recommended-knowledge]")?.addEventListener("click", applyRecommendedKnowledgeAndRunQA);
      content.querySelector("[data-next-step]")?.addEventListener("click", function () { goToStep("preview"); });
    }

    async function applyRecommendedKnowledgeAndRunQA() {
      const button = content.querySelector("[data-apply-recommended-knowledge]");
      if (button) button.disabled = true;
      try {
        const data = await api("/api/projects/" + encodeURIComponent(state.id) + "/knowledge/recommended", { method: "POST", body: "{}" });
        state = data.project;
        qaKnowledgeWarningOpen = false;
        if (!(state.knowledge || []).some(function (item) { return item.enabled; })) {
          showToast("適用できる推奨Knowledgeがありません", "error");
          render();
          return;
        }
        showToast("推奨Knowledgeを適用しました");
        await runQualityCheck(true);
      } catch (error) {
        showToast(error.message, "error");
        render();
      }
    }

    async function runQualityCheck(skipPreflight) {
      if (!skipPreflight && (state.knowledge || []).length === 0) {
        qaKnowledgeWarningOpen = true;
        render();
        return;
      }
      const button = content.querySelector("[data-run-quality-check]");
      if (!button || button.disabled) return;
      button.disabled = true;
      const original = button.textContent;
      button.textContent = "確認中…";
      showProcessingDialog({
        message: "QAを実行しています…",
        progress: "原作・ネーム・画像状態を確認しています",
        submessage: "選択したKnowledgeとの整合性も確認しています。"
      });
      try {
        const data = await api("/api/projects/" + encodeURIComponent(state.id) + "/quality-check", { method: "POST", body: "{}" });
        state = data.project;
        showToast("品質チェックが完了しました");
        render();
      } catch (error) {
        showToast(error.message, "error");
        button.disabled = false;
        button.textContent = original;
      } finally { hideProcessingDialog(); }
    }

    function renderPreview() {
      const pages = state.storyboard || [];
      if (selectedPageIndex >= pages.length) selectedPageIndex = Math.max(0, pages.length - 1);
      const page = pages[selectedPageIndex];
      const picker = pages.map(function (item, index) { return '<button type="button" class="page-picker-button' + (index === selectedPageIndex ? " active" : "") + '" data-page-index="' + index + '"><strong>' + String(index + 1).padStart(2, "0") + '</strong><span>' + escapeHtml(item.title || "ページ") + '</span></button>'; }).join("");
      const direction = htmlDirection(state.settings);
      const isEnglish = canonicalLanguage(state.settings) === "en";
      const previousLabel = isEnglish ? "← 前のページ" : "→ 前のページ";
      const nextLabel = isEnglish ? "次のページ →" : "次のページ ←";
      const navigation = '<div class="preview-navigation" data-reading-direction="' + escapeAttr(readingDirectionKey(state.settings)) + '"><button type="button" class="secondary-button compact-button" data-preview-prev' + (selectedPageIndex <= 0 ? " disabled" : "") + '>' + previousLabel + '</button><span aria-live="polite">' + escapeHtml(languageLabel(state.settings)) + ' / ' + escapeHtml(readingDirectionLabel(state.settings)) + ' / ' + (selectedPageIndex + 1) + ' / ' + pages.length + '</span><button type="button" class="secondary-button compact-button" data-preview-next' + (selectedPageIndex >= pages.length - 1 ? " disabled" : "") + '>' + nextLabel + '</button></div>';
      content.innerHTML = heading("完成ページを読む", "ページ単位で確認し、セリフの読みやすさと流れを見ます。") + (pages.length ? '<div class="preview-layout"><nav class="page-picker" dir="' + direction + '" aria-label="ページ選択">' + picker + '</nav><div>' + navigation + pageStage(page) + '</div></div>' + nextButton("export", "書き出しへ") : '<section class="surface-panel empty-panel"><h3>プレビューできるページがありません</h3><p>ネームを作成してからコマを生成してください。</p></section>');
      content.querySelectorAll("[data-page-index]").forEach(function (button) { button.addEventListener("click", function () { selectedPageIndex = Number(button.dataset.pageIndex); render(); }); });
      content.querySelector("[data-preview-prev]")?.addEventListener("click", function () { selectedPageIndex = Math.max(0, selectedPageIndex - 1); render(); });
      content.querySelector("[data-preview-next]")?.addEventListener("click", function () { selectedPageIndex = Math.min(pages.length - 1, selectedPageIndex + 1); render(); });
      content.querySelector("[data-next-step]")?.addEventListener("click", function () { goToStep("export"); });
    }

    function renderExport() {
      const entries = allPanels();
      const unfinished = entries.filter(function (item) { return item.panel.generation_status !== "completed"; }).length;
      content.innerHTML = heading("作品を書き出す", "編集内容を確認して、読みやすい形で保存します。") + '<div class="export-layout"><section class="surface-panel panel-padding"><div class="source-meta"><span>' + (state.storyboard || []).length + 'ページ</span><span>' + entries.length + 'コマ</span><span>' + generatedCount() + 'コマ生成済み</span></div><h3>書き出し形式</h3><p class="panel-lead">PDFは画像とセリフを合成した作品ファイル、ZIPは生成画像と編集用データを含みます。</p><div class="export-options"><div class="export-option"><span class="export-icon">PDF</span><div><strong>PDF</strong><p>ページ送りで読める作品ファイル</p></div><button type="button" class="secondary-button compact-button" data-export-format="pdf">PDFを作る</button></div><div class="export-option"><span class="export-icon">ZIP</span><div><strong>ページ画像ZIP</strong><p>生成画像とProject JSONの一式</p></div><button type="button" class="secondary-button compact-button" data-export-format="zip">ZIPを作る</button></div></div><div id="export-result"></div></section><aside class="surface-panel panel-padding"><h3>最終チェック</h3><div class="stat-rail"><div class="stat-tile"><span>本文</span><strong>✓</strong></div><div class="stat-tile"><span>ネーム</span><strong>' + ((state.storyboard || []).length ? "✓" : "－") + '</strong></div><div class="stat-tile"><span>未生成コマ</span><strong>' + unfinished + '</strong></div></div>' + (unfinished ? '<div class="export-warning">未生成のコマが' + unfinished + '件あります。書き出しはできますが、先に生成すると完成度を確認できます。</div>' : '<div class="export-success">すべてのコマにアートがあります。書き出し準備ができています。</div>') + '</aside></div>';
      content.querySelectorAll("[data-export-format]").forEach(function (button) { button.addEventListener("click", function () { doExport(button.dataset.exportFormat, button); }); });
    }

    async function doExport(format, button) {
      button.disabled = true;
      const original = button.textContent;
      button.textContent = "作成中…";
      showProcessingDialog({
        message: format === "pdf" ? "PDFを書き出しています…" : "ZIPを書き出しています…",
        progress: "ページ画像と編集データをまとめています",
        submessage: "書き出しが完了するまでお待ちください。"
      });
      try {
        const data = await api("/api/projects/" + encodeURIComponent(state.id) + "/export", { method: "POST", body: JSON.stringify({ format: format }) });
        const result = content.querySelector("#export-result");
        if (result) result.innerHTML = (data.warning ? '<div class="export-warning">' + escapeHtml(data.warning) + '</div>' : "") + '<div class="export-success">書き出しが完了しました。<a href="' + escapeAttr(data.download_url) + '">ファイルをダウンロード</a></div>';
        showToast(format.toUpperCase() + "を書き出しました");
      } catch (error) { showToast(error.message, "error"); }
      finally {
        hideProcessingDialog();
        button.disabled = false;
        button.textContent = original;
      }
    }

    function render() {
      renderProgress();
      if (activeStep === "story") renderStory();
      else if (activeStep === "knowledge") renderKnowledge();
      else if (activeStep === "analysis") renderAnalysis();
      else if (activeStep === "settings") renderSettings();
      else if (activeStep === "characters") renderCharacters();
      else if (activeStep === "storyboard") renderStoryboard();
      else if (activeStep === "generate") renderGenerate();
      else if (activeStep === "edit") renderEdit();
      else if (activeStep === "qa") renderQA();
      else if (activeStep === "preview") renderPreview();
      else if (activeStep === "export") renderExport();
      if (activeStep === "edit" || activeStep === "preview") scheduleMangaTextFit();
    }

    render();
    restoreCharacterJobState();
    restoreStoryboardJobState();
    if (activeStep === "generate") restorePanelGenerationState();
  }
})();
