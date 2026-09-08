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
    submessage: document.getElementById("processing-dialog-submessage")
  };
  let processingDialogOpen = false;
  let processingDialogPreviousFocus = null;

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
      processingDialogElements.dialog?.focus();
    }
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
    let knowledgeRequestId = 0;
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

    async function api(url, options) {
      const response = await fetch(url, { headers: { Accept: "application/json", "Content-Type": "application/json", ...(options?.headers || {}) }, ...options });
      const data = await response.json().catch(function () { return {}; });
      if (!response.ok) throw new Error(data.detail || "サーバーとの通信に失敗しました");
      return data;
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
          const scopes = selected.scope || ["all"];
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
        if (!enabled) return;
        const mode = card.querySelector("[data-knowledge-mode]")?.value || "follow_latest";
        const version = card.querySelector("[data-knowledge-version]")?.value || null;
        if (mode === "pinned" && !version) invalid = "固定するVersionを選択してください";
        const priority = Number(card.querySelector("[data-knowledge-priority]")?.value || 50);
        const scope = Array.from(card.querySelector("[data-knowledge-scope]")?.selectedOptions || []).map(function (option) { return option.value; });
        selections.push({ knowledge_document_id: card.dataset.documentId, enabled: true, priority: Number.isFinite(priority) ? priority : 50, mode: mode, selected_version_id: mode === "pinned" ? version : null, scope: scope.length ? scope : ["all"] });
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

    function renderCharacters() {
      const characters = state.characters || [];
      const fields = function (character) {
        const textField = function (key, label, rows) { return '<label class="editor-label">' + escapeHtml(label) + '<textarea data-character-field="' + key + '" rows="' + rows + '">' + escapeHtml(character[key] || "") + '</textarea></label>'; };
        return textField("appearance", "外見", 3) + textField("clothing", "服装", 2) + textField("personality", "性格", 2) + textField("distinguishing_features", "識別ポイント", 2) + textField("visual_prompt", "生成用の一貫性メモ", 2) + textField("negative_constraints", "変えない制約", 2);
      };
      const cards = characters.map(function (character, index) {
        return '<article class="surface-panel character-card" data-character-id="' + escapeAttr(character.id) + '"><div class="character-card-header"><div><h3>' + escapeHtml(character.name || "名前未設定") + '</h3><p>' + escapeHtml(character.role || "役割未設定") + ' / ' + escapeHtml(character.age_range || "年齢未設定") + '</p></div><span class="character-stamp">' + String(index + 1).padStart(2, "0") + '</span></div><div class="character-fields">' + '<label class="editor-label">名前<input data-character-field="name" value="' + escapeAttr(character.name || "") + '"></label>' + '<label class="editor-label">役割<input data-character-field="role" value="' + escapeAttr(character.role || "") + '"></label>' + fields(character) + '</div></article>';
      }).join("");
      content.innerHTML = heading("キャラクターを固定する", "同一人物の外見・服装を後続コマへ引き継ぐための設定です。") + (characters.length ? '<div class="character-grid">' + cards + '</div><div class="save-row"><button type="button" class="secondary-button compact-button" data-regenerate-characters>人物設定を作り直す</button><button type="button" class="primary-button compact-button" data-save-characters>キャラクターを保存</button></div>' + nextButton("storyboard", "ネームを作る") : '<section class="surface-panel empty-panel"><h3>キャラクターバイブルを作る</h3><p>解析結果から、同じ人物を描き続けるための基準を作成します。</p><button type="button" class="primary-button compact-button" data-generate-characters>生成する</button></section>');
      content.querySelector("[data-generate-characters]")?.addEventListener("click", generateCharacters);
      content.querySelector("[data-regenerate-characters]")?.addEventListener("click", generateCharacters);
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
      const button = content.querySelector("[data-generate-characters], [data-regenerate-characters]");
      if (button) { button.disabled = true; button.textContent = "人物設定を作成中…"; }
      showProcessingDialog({
        message: "キャラクター設定を生成しています…",
        progress: "人物の外見と関係性を整理しています",
        submessage: "後続のコマでも同じ人物として描ける設定を作成しています。"
      });
      try {
        const data = await api("/api/projects/" + encodeURIComponent(state.id) + "/characters", { method: "POST", body: "{}" });
        state = data.project;
        showToast("キャラクターバイブルを作成しました");
        render();
      } catch (error) { showToast(error.message, "error"); if (button) button.disabled = false; }
      finally { hideProcessingDialog(); }
    }

    function panelTemplate(pageId, panel, index) {
      const input = function (key, label, value, full) { return '<label class="editor-label ' + (full ? "full" : "") + '">' + escapeHtml(label) + '<textarea data-panel-field="' + key + '" rows="' + (full ? "3" : "2") + '">' + escapeHtml(Array.isArray(value) ? textAreaValue(value) : value || "") + '</textarea></label>'; };
      const scalar = function (key, label, value) { return '<label class="editor-label">' + escapeHtml(label) + '<input data-panel-field="' + key + '" value="' + escapeAttr(value || "") + '"></label>'; };
      return '<article class="storyboard-panel" data-panel-id="' + escapeAttr(panel.id) + '"><span class="panel-index">' + String(panel.order || index + 1).padStart(2, "0") + '</span><div><div class="panel-editor-grid">' + input("description", "コマの意図", panel.description, true) + scalar("shot_type", "カメラ", panel.shot_type) + scalar("action", "行動", panel.action) + scalar("expression", "表情", panel.expression) + scalar("background", "背景", panel.background) + scalar("characters", "登場人物（カンマ区切り）", (panel.characters || []).join(", ")) + input("dialogue", "セリフ（1行1つ）", panel.dialogue, false) + input("narration", "ナレーション", panel.narration, false) + input("sfx", "効果音", panel.sfx, false) + '</div><div class="panel-mini-actions"><button type="button" class="text-button" data-panel-move="up" data-page-id="' + escapeAttr(pageId) + '" data-panel-id="' + escapeAttr(panel.id) + '">↑ 上へ</button><button type="button" class="text-button" data-panel-move="down" data-page-id="' + escapeAttr(pageId) + '" data-panel-id="' + escapeAttr(panel.id) + '">↓ 下へ</button><button type="button" class="text-button danger-button" data-panel-delete data-page-id="' + escapeAttr(pageId) + '" data-panel-id="' + escapeAttr(panel.id) + '">削除</button><button type="button" class="primary-button compact-button" data-save-panel data-page-id="' + escapeAttr(pageId) + '" data-panel-id="' + escapeAttr(panel.id) + '">コマを保存</button></div></div></article>';
    }

    function renderStoryboard() {
      const pages = state.storyboard || [];
      const pageMarkup = pages.map(function (page, pageIndex) {
        const panels = (page.panels || []).map(function (panel, panelIndex) { return panelTemplate(page.id, panel, panelIndex); }).join("");
        const layoutOptions = [{value:"hero", label:"Hero"}, {value:"classic", label:"Classic"}, {value:"grid", label:"Grid"}, {value:"wide", label:"Wide"}].map(function (option) { return '<option value="' + option.value + '"' + (option.value === (page.layout || "classic") ? " selected" : "") + '>' + option.label + '</option>'; }).join("");
        return '<article class="page-card" data-page-id="' + escapeAttr(page.id) + '"><header class="page-card-header"><div class="page-card-title"><span class="page-number-badge">' + String(pageIndex + 1).padStart(2, "0") + '</span><div><h3>' + escapeHtml(page.title || "ページ") + '</h3><p>' + (page.panels || []).length + 'コマ / ' + escapeHtml(page.layout || "classic") + '</p></div></div><div class="page-card-actions"><label class="page-layout-control">レイアウト<select data-page-layout data-page-id="' + escapeAttr(page.id) + '">' + layoutOptions + '</select></label><button type="button" class="text-button" data-page-move="up" data-page-id="' + escapeAttr(page.id) + '">↑</button><button type="button" class="text-button" data-page-move="down" data-page-id="' + escapeAttr(page.id) + '">↓</button><button type="button" class="text-button danger-button" data-page-delete data-page-id="' + escapeAttr(page.id) + '">ページ削除</button></div></header><div class="panel-list">' + panels + '</div><div class="add-row"><button type="button" class="outline-button" data-add-panel data-page-id="' + escapeAttr(page.id) + '">＋ コマを追加</button></div></article>';
      }).join("");
      const body = pages.length ? '<div class="storyboard-list">' + pageMarkup + '</div><div class="save-row"><button type="button" class="outline-button" data-add-page>＋ ページを追加</button><button type="button" class="primary-button compact-button" data-generate-storyboard>ネームを作り直す</button></div>' + nextButton("generate", "コマ生成へ") : '<section class="surface-panel empty-panel"><h3>ページとコマを設計する</h3><p>解析、設定、人物情報をもとに、読める流れを組み立てます。</p><button type="button" class="primary-button compact-button" data-generate-storyboard>ネームを生成する</button></section>';
      const orderNote = '<div class="reading-order-note"><strong>' + escapeHtml(languageLabel(state.settings)) + ' / ' + escapeHtml(readingDirectionLabel(state.settings)) + '</strong><span>Panel.orderは読者の論理読順です。Knowledgeの逆方向指定よりProject設定を優先します。</span></div>';
      content.innerHTML = heading("ネームを編集する", "ページをまたぐ展開と、コマごとの視線の流れを確認します。") + orderNote + body;
      bindStoryboardEvents();
    }

    function newPanel(order) {
      return { id: uuid("panel"), order: order, description: "追加したコマの意図を入力", shot_type: "バストアップ", characters: [], action: "", expression: "", background: "", dialogue: [], narration: [], sfx: [], bubble_order: [], narration_order: [], sfx_order: [], generation_prompt: "", image_url: null, generation_status: "not_started", generation_error: null, revision: 0, crop_mode: "fit" };
    }

    function bindStoryboardEvents() {
      content.querySelector("[data-generate-storyboard]")?.addEventListener("click", generateStoryboard);
      content.querySelector("[data-add-page]")?.addEventListener("click", function () {
        const pages = [...(state.storyboard || [])];
        pages.push({ id: uuid("page"), page_number: pages.length + 1, title: "追加ページ", layout: "classic", panels: [newPanel(1)] });
        saveStoryboard(pages, "ページを追加しました");
      });
      content.querySelectorAll("[data-page-layout]").forEach(function (select) { select.addEventListener("change", function () {
        const pages = structuredClone(state.storyboard); const page = pages.find(function (item) { return item.id === select.dataset.pageId; }); if (!page) return; page.layout = select.value; saveStoryboard(pages, "ページレイアウトを更新しました");
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

    async function pollStoryboardJob(jobId, operationMessage, button) {
      for (let attempt = 0; attempt < 120; attempt += 1) {
        await new Promise(function (resolve) { window.setTimeout(resolve, 2000); });
        const data = await api("/api/projects/" + encodeURIComponent(state.id) + "/generation/status");
        const job = (data.jobs || []).find(function (item) { return item.id === jobId; });
        if (!job) throw new Error("Storyboardの処理状態を取得できませんでした");
        if (job.status === "completed") {
          await fetchProject(false);
          render();
          showToast("ページとコマの構成を作成しました");
          return;
        }
        if (job.status === "failed") {
          throw new Error(job.error || "Storyboardの生成に失敗しました");
        }
        updateProcessingDialog({
          message: operationMessage,
          progress: job.status === "queued" ? "生成キューで順番を待っています" : "AIがページとコマの構成を作成しています",
          submessage: "完了後に生成結果を保存します。画面を閉じても処理は継続します。"
        });
      }
      throw new Error("Storyboardの処理状況の確認がタイムアウトしました。しばらくしてから再読み込みしてください");
    }

    async function generateStoryboard() {
      if (!state.analysis) { showToast("先に物語解析を生成してください", "error"); return; }
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
          updateProcessingDialog({
            message: "ストーリーボードを生成しています…",
            progress: "生成キューへ登録しました",
            submessage: "Render上のバックグラウンド処理でネームを作成しています。"
          });
          await pollStoryboardJob(data.job.id, "ストーリーボードを生成しています…", button);
          return;
        }
        showToast("ページとコマの構成を作成しました");
        render();
      } catch (error) { showToast(error.message, "error"); if (button) button.disabled = false; }
      finally { hideProcessingDialog(); }
    }

    function renderGenerationRows() {
      return allPanels().map(function (item) {
        const panel = item.panel;
        const status = panel.generation_status || "not_started";
        const action = status === "failed" ? '<button type="button" class="text-button" data-retry-panel="' + escapeAttr(panel.id) + '">再試行</button>' : status === "completed" ? '<button type="button" class="text-button" data-regenerate-panel="' + escapeAttr(panel.id) + '">再生成</button>' : "";
        const thumb = panel.image_url ? '<img src="' + escapeAttr(panel.image_url) + '" alt="">' : '<span>' + escapeHtml(String((item.page.page_number || 1) + " / " + (panel.order || (item.page.panels || []).indexOf(panel) + 1))) + '</span>';
        const referenceLabel = (panel.knowledge_refs || []).map(function (ref) { return (ref.title || "Knowledge") + " v" + (ref.version_number || "?"); }).join(", ");
        const generationModel = panel.generation_metadata?.actual_model ? " / 実行モデル: " + escapeHtml(panel.generation_metadata.actual_model) + (panel.generation_metadata.fallback ? "（Astraからフォールバック）" : "") : "";
        const metadata = 'ページ ' + escapeHtml(item.page.page_number) + ' / ' + escapeHtml(panel.shot_type || "ショット未設定") + ' / Revision ' + escapeHtml(panel.revision || 0) + generationModel + (referenceLabel ? " / " + escapeHtml(referenceLabel) : "") + (panel.generation_error ? " / " + escapeHtml(panel.generation_error) : "");
        return '<div class="generation-panel-row"><div class="generation-thumb">' + thumb + '</div><div><strong>' + escapeHtml(panel.description || "コマの説明") + '</strong><small>' + metadata + '</small></div><span class="panel-status panel-status-' + escapeAttr(status) + '">' + escapeHtml(panelStatusLabels[status] || status) + '</span>' + action + '</div>';
      }).join("");
    }

    function renderGenerate() {
      const panels = allPanels();
      const generated = panels.filter(function (item) { return item.panel.generation_status === "completed"; }).length;
      const failed = panels.filter(function (item) { return item.panel.generation_status === "failed"; }).length;
      content.innerHTML = heading("コマを生成する", "必要なコマだけを選び、生成後も一枚ずつ再生成できます。") + (panels.length ? '<div class="generate-rail"><section class="surface-panel panel-padding"><div class="generation-toolbar"><p>' + panels.length + 'コマ中 ' + generated + 'コマを生成済み</p><div class="generation-actions"><button type="button" class="secondary-button compact-button" data-retry-failed' + (failed ? "" : " disabled") + '>失敗したコマを再試行</button><button type="button" class="primary-button compact-button" data-generate-all>未生成をまとめて生成</button></div></div><div class="panel-status-list">' + renderGenerationRows() + '</div></section><aside class="generation-summary"><div class="surface-panel"><h3>今回の対象</h3><div class="generation-summary-number">' + panels.length + '</div><p>コマ。デモモードではすぐに確認できます。</p></div><div class="surface-panel"><h3>生成ルール</h3><p class="cost-note">キャラクター設定を毎回参照し、セリフは画像に描かずアプリ側で合成します。</p></div></aside></div>' + nextButton("edit", "編集画面へ") : '<section class="surface-panel empty-panel"><h3>先にネームを作成してください</h3><p>ページ・コマ構成ができると、必要な画像だけ生成できます。</p><button type="button" class="primary-button compact-button" data-goto-storyboard>ネームへ戻る</button></section>');
      content.querySelector("[data-generate-all]")?.addEventListener("click", function () { queueGeneration([], false, false); });
      content.querySelector("[data-retry-failed]")?.addEventListener("click", function () { queueGeneration([], true, false); });
      content.querySelectorAll("[data-retry-panel]").forEach(function (button) { button.addEventListener("click", function () { queueGeneration([button.dataset.retryPanel], true, true); }); });
      content.querySelectorAll("[data-regenerate-panel]").forEach(function (button) { button.addEventListener("click", function () { queueGeneration([button.dataset.regeneratePanel], false, true); }); });
      content.querySelector("[data-goto-storyboard]")?.addEventListener("click", function () { goToStep("storyboard"); });
      content.querySelector("[data-next-step]")?.addEventListener("click", function () { goToStep("edit"); });
    }

    async function queueGeneration(panelIds, retryFailed, force) {
      const buttons = content.querySelectorAll("[data-generate-all], [data-retry-failed], [data-retry-panel], [data-regenerate-panel]");
      buttons.forEach(function (button) { button.disabled = true; });
      const operationMessage = force && panelIds.length === 1 ? "漫画画像を再生成しています…" : "漫画画像を生成しています…";
      showProcessingDialog({
        message: operationMessage,
        progress: "生成対象を確認しています",
        submessage: "選択したコマだけを処理し、完了した画像から保存します。"
      });
      try {
        const data = await api("/api/projects/" + encodeURIComponent(state.id) + "/generate", { method: "POST", body: JSON.stringify({ panel_ids: panelIds, retry_failed: retryFailed, force: force }) });
        const queuedPanelIds = Array.isArray(data.queued_panel_ids) ? data.queued_panel_ids.filter(Boolean) : [];
        if (!queuedPanelIds.length) {
          hideProcessingDialog();
          showToast("生成対象はありません。完了済みのコマは再利用されます");
          await fetchProject(true);
          return;
        }
        showToast(queuedPanelIds.length + "コマを生成キューに追加しました");
        updateProcessingDialog({ progress: queuedPanelIds.length + "コマを順番に生成します" });
        await fetchProject(true);
        await pollGeneration(queuedPanelIds, operationMessage);
      } catch (error) {
        hideProcessingDialog();
        showToast(error.message, "error");
        render();
      }
    }

    async function pollGeneration(targetPanelIds, operationMessage) {
      if (polling) return;
      polling = true;
      const targetIds = new Set(targetPanelIds || []);
      let finished = false;
      try {
        for (let attempt = 0; attempt < 40; attempt += 1) {
          await new Promise(function (resolve) { window.setTimeout(resolve, 500); });
          const data = await api("/api/projects/" + encodeURIComponent(state.id) + "/generation/status");
          const byId = Object.fromEntries(data.panels.map(function (panel) { return [panel.id, panel]; }));
          (state.storyboard || []).forEach(function (page) { (page.panels || []).forEach(function (panel) { const update = byId[panel.id]; if (update) { panel.generation_status = update.status; panel.generation_error = update.error; panel.image_url = update.image_url; panel.revision = update.revision; panel.generation_metadata = update.generation_metadata; } }); });
          const active = data.panels.some(function (panel) { return panel.status === "queued" || panel.status === "processing"; });
          const tracked = targetIds.size ? data.panels.filter(function (panel) { return targetIds.has(panel.id); }) : data.panels;
          const completed = tracked.filter(function (panel) { return panel.status === "completed"; }).length;
          const failed = tracked.filter(function (panel) { return panel.status === "failed"; }).length;
          const currentPanel = tracked.find(function (panel) { return panel.status === "queued" || panel.status === "processing"; });
          if (!active || !currentPanel) {
            finished = true;
            await fetchProject(false);
            render();
            if (failed) {
              updateProcessingDialog({ message: "一部の漫画画像を生成できませんでした", progress: completed + "コマ完了 / " + failed + "コマ失敗", submessage: "失敗したコマは生成画面から再試行できます。" });
              showToast(failed + "コマの生成に失敗しました。再試行できます", "error");
            } else {
              updateProcessingDialog({ message: "漫画画像の生成が完了しました", progress: completed + "コマを保存しました", submessage: "生成結果を確認できます。" });
            }
            break;
          }
          const total = targetIds.size || tracked.length;
          const current = Math.min(completed + 1, Math.max(1, total));
          updateProcessingDialog({
            message: operationMessage || "漫画画像を生成しています…",
            progress: total + "コマ中 " + current + "コマ目を生成しています",
            submessage: currentPanel.status === "queued" ? "生成キューで順番を待っています。" : "画像を生成し、保存しています。"
          });
          if (activeStep === "generate") render();
        }
        if (!finished) showToast("生成状況の確認がタイムアウトしました。しばらくしてから再読み込みしてください", "error");
      } catch (error) {
        showToast(error.message, "error");
      } finally {
        polling = false;
        hideProcessingDialog();
      }
    }

    function pageStage(page) {
      if (!page) return '<div class="preview-frame-wrap"><p>ページがありません</p></div>';
      const panels = page.panels || [];
      let layout = page.layout || "classic";
      if (panels.length === 1) layout = "hero";
      else if (panels.length >= 3) layout = "grid";
      const panelHtml = panels.map(function (panel, index) {
        const position = panelVisualPosition(index, panels.length, state.settings);
        const visualColumn = layout === "wide" && index === 0 ? "1 / -1" : String(position.column);
        const placement = ' style="grid-column:' + visualColumn + ';grid-row:' + position.row + ';"';
        const imageClass = panel.crop_mode === "fill" ? "panel-image-fill" : "panel-image-fit";
        const logicalOrder = panel.order || index + 1;
        const image = panel.image_url ? '<img class="' + imageClass + '" src="' + escapeAttr(panel.image_url) + '" alt="ページ' + escapeAttr(page.page_number) + ' コマ' + escapeAttr(logicalOrder) + '">' : '<div class="manga-panel-placeholder">ARTWORK<br>未生成</div>';
        const dialogue = (panel.dialogue || []).filter(Boolean);
        const narration = (panel.narration || []).filter(Boolean);
        const sfx = (panel.sfx || []).filter(Boolean);
        const bubbles = dialogue.map(function (text, bubbleIndex) {
          const side = bubbleSide(bubbleIndex, state.settings);
          const top = 10 + Math.floor(bubbleIndex / 2) * 49;
          return '<span class="speech-bubble bubble-side-' + side + '" style="top:' + top + 'px" data-bubble-order="' + (bubbleIndex + 1) + '">' + escapeHtml(text).replace(/\n/g, "<br>") + '</span>';
        }).join("");
        const narrationMarkup = narration.map(function (text, narrationIndex) {
          const side = bubbleSide(narrationIndex, state.settings);
          const bottom = 8 + narrationIndex * 22;
          return '<span class="page-narration narration-side-' + side + '" style="bottom:' + bottom + 'px" data-narration-order="' + (narrationIndex + 1) + '">' + escapeHtml(text) + '</span>';
        }).join("");
        const sfxMarkup = sfx.map(function (text, sfxIndex) {
          const side = bubbleSide(sfxIndex, state.settings);
          const top = 8 + sfxIndex * 18;
          return '<span class="page-sfx sfx-side-' + side + '" style="top:' + top + 'px" data-sfx-order="' + (sfxIndex + 1) + '">' + escapeHtml(text) + '</span>';
        }).join("");
        return '<div class="manga-panel' + (layout === "wide" && index === 0 ? " wide" : "") + '" data-reading-order="' + escapeAttr(logicalOrder) + '" data-visual-column="' + position.column + '" data-visual-row="' + position.row + '"' + placement + '>' + image + bubbles + narrationMarkup + sfxMarkup + '</div>';
      }).join("");
      const direction = htmlDirection(state.settings);
      return '<div class="preview-frame-wrap" data-language="' + escapeAttr(canonicalLanguage(state.settings)) + '"><div class="manga-page layout-' + escapeAttr(layout) + '" dir="' + direction + '" data-reading-direction="' + escapeAttr(readingDirectionKey(state.settings)) + '">' + panelHtml + '</div></div>';
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
      const form = panel ? '<form class="edit-form" id="panel-edit-form"><h3>コマの仕上げ</h3><label class="editor-label">生成プロンプト<textarea class="editor-textarea" data-edit-field="generation_prompt" rows="7">' + escapeHtml(panel.generation_prompt || "") + '</textarea><small>画像に文字は描かず、アプリ側でセリフを載せます。</small></label><label class="editor-label">セリフ<textarea class="editor-textarea" data-edit-field="dialogue" rows="4">' + escapeHtml(textAreaValue(panel.dialogue)) + '</textarea></label><label class="editor-label">ナレーション<textarea class="editor-textarea" data-edit-field="narration" rows="3">' + escapeHtml(textAreaValue(panel.narration)) + '</textarea></label><label class="editor-label">表示方法<select data-edit-field="crop_mode"><option value="fit"' + (panel.crop_mode === "fit" ? " selected" : "") + '>全体を表示</option><option value="fill"' + (panel.crop_mode === "fill" ? " selected" : "") + '>枠に合わせる</option></select></label><div class="save-row"><button type="submit" class="primary-button compact-button">変更を保存</button><button type="button" class="secondary-button compact-button" data-regenerate-selected>このコマを再生成</button></div></form>' : '<div class="empty-panel"><h3>編集するコマを選んでください</h3></div>';
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
      const refs = (result?.knowledge_refs || []).map(function (ref) { return '<li><strong>' + escapeHtml(ref.title) + '</strong><span>Version ' + escapeHtml(ref.version_number) + ' / ' + escapeHtml((ref.chunk_ids || []).length) + ' Chunk</span></li>'; }).join("");
      const resultMarkup = result ? '<section class="surface-panel panel-padding qa-result"><div class="qa-result-header"><div><p class="eyebrow">QUALITY REPORT</p><h3>制作状態: ' + escapeHtml(statusLabel) + '</h3><p>確認日時: ' + escapeHtml(result.checked_at || "") + '</p></div><span class="qa-result-badge qa-result-' + escapeAttr(result.status || "attention") + '">' + escapeHtml(statusLabel) + '</span></div><ul class="qa-check-list">' + checks + '</ul>' + (issues ? '<div class="qa-issues"><h4>修正が必要な項目</h4><ul>' + issues + '</ul></div>' : "") + (warnings ? '<div class="qa-issues"><h4>確認してください</h4><ul>' + warnings + '</ul></div>' : "") + (refs ? '<div class="knowledge-reference-list"><h4>今回参照したKnowledge</h4><ul>' + refs + '</ul></div>' : '<div class="knowledge-workspace-note"><strong>Knowledge参照なし</strong><span>このProjectではKnowledgeを選択していないか、Scopeに該当するChunkがありません。</span></div>') + '</section>' : '<section class="surface-panel empty-panel"><h3>書き出し前に品質を確認する</h3><p>本文、ネーム、画像状態、ページ内のコマ数とKnowledgeの解決状況を確認します。判定は決定的なチェックを中心に行います。</p></section>';
      content.innerHTML = heading("Knowledge-aware QA", "書き出し前に制作状態と、どのKnowledge Versionを参照したかを確認します。") + '<div class="qa-toolbar"><div class="knowledge-workspace-note"><strong>確認対象</strong><span>原作の意図を変える判定ではなく、編集を続けるための状態チェックです。</span></div><button type="button" class="primary-button compact-button" data-run-quality-check>品質を確認する</button></div>' + resultMarkup + nextButton("preview", "Previewを見る");
      content.querySelector("[data-run-quality-check]")?.addEventListener("click", runQualityCheck);
      content.querySelector("[data-next-step]")?.addEventListener("click", function () { goToStep("preview"); });
    }

    async function runQualityCheck() {
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
    }

    render();
  }
})();
