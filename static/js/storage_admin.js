(() => {
  "use strict";

  const root = document.querySelector("[data-storage-cleanup]");
  if (!root) return;

  const dryRunButton = root.querySelector("[data-storage-cleanup-dry-run]");
  const result = root.querySelector("[data-storage-cleanup-result]");
  const form = root.querySelector("[data-storage-cleanup-form]");
  const checkbox = root.querySelector("[data-storage-cleanup-checkbox]");
  const confirmation = root.querySelector("[data-storage-cleanup-confirmation]");
  const submit = root.querySelector("[data-storage-cleanup-submit]");
  const latest = root.querySelector("[data-storage-cleanup-latest]");
  const confirmedSummary = root.querySelector("[data-storage-cleanup-confirmed]");
  const excludedSummary = root.querySelector("[data-storage-cleanup-excluded]");
  const categorySummary = root.querySelector("[data-storage-cleanup-categories]");

  const formatBytes = (value) => {
    let amount = Math.max(0, Number(value) || 0);
    const units = ["B", "KB", "MB", "GB", "TB"];
    let index = 0;
    while (amount >= 1024 && index < units.length - 1) {
      amount /= 1024;
      index += 1;
    }
    return `${amount.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
  };

  const setMessage = (message, type = "") => {
    if (!result) return;
    result.textContent = message;
    result.className = `storage-cleanup-result${type ? ` ${type}` : ""}`;
  };

  const readError = async (response) => {
    try {
      const body = await response.json();
      return body.detail || "Cleanup処理に失敗しました";
    } catch (_error) {
      return "Cleanup処理に失敗しました";
    }
  };

  const updateSubmitState = () => {
    if (submit) submit.disabled = !(checkbox?.checked && confirmation?.value.trim() === "DELETE");
  };

  dryRunButton?.addEventListener("click", async () => {
    dryRunButton.disabled = true;
    setMessage("最新状態を再監査しています…");
    try {
      const response = await fetch("/api/admin/storage/cleanup/dry-run", {
        method: "POST",
        headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" },
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error(await readError(response));
      const payload = await response.json();
      const plan = payload.plan || {};
      const dryRun = payload.dry_run || {};
      root.dataset.storageCleanupPlanId = plan.id || dryRun.plan_id || "";
      if (confirmedSummary) confirmedSummary.textContent = `${Number(dryRun.confirmed_orphan_count || 0)}件`;
      if (excludedSummary) excludedSummary.textContent = `${Number(dryRun.excluded_count || 0)}件`;
      if (categorySummary) {
        const categories = dryRun.category_counts || {};
        categorySummary.textContent = `${Number(categories.assets || 0)}件 / ${Number(categories.exports || 0)}件`;
      }
      if (latest) latest.textContent = `最新Plan: ${plan.id || "-"}（${plan.status || "PLANNED"}、${Number(dryRun.confirmed_orphan_count || 0)}件）`;
      if (form) form.hidden = !root.dataset.storageCleanupPlanId;
      setMessage(`dry-run完了：確認済み孤児 ${Number(dryRun.confirmed_orphan_count || 0)}件、回収見込み ${formatBytes(dryRun.reclaimable_bytes)}。ファイルは変更していません。`, "success");
    } catch (error) {
      setMessage(error.message || "dry-runに失敗しました", "error");
    } finally {
      dryRunButton.disabled = false;
      updateSubmitState();
    }
  });

  checkbox?.addEventListener("change", updateSubmitState);
  confirmation?.addEventListener("input", updateSubmitState);

  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const planId = root.dataset.storageCleanupPlanId;
    if (!planId || !checkbox?.checked || confirmation?.value.trim() !== "DELETE") {
      setMessage("チェックボックスとDELETEの入力が必要です。", "error");
      updateSubmitState();
      return;
    }
    if (submit) submit.disabled = true;
    setMessage("実行直前に対象を再検証しています…");
    try {
      const response = await fetch("/api/admin/storage/cleanup/execute", {
        method: "POST",
        headers: {
          "Accept": "application/json",
          "Content-Type": "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
        credentials: "same-origin",
        body: JSON.stringify({ plan_id: planId, confirmation: "DELETE" }),
      });
      if (!response.ok) throw new Error(await readError(response));
      const payload = await response.json();
      const summary = payload.summary || {};
      setMessage(`Cleanup完了：削除 ${Number(summary.deleted_count || 0)}件、スキップ ${Number(summary.skipped_count || 0)}件、失敗 ${Number(summary.failed_count || 0)}件、回収 ${formatBytes(summary.reclaimed_bytes)}。`, "success");
      if (latest) latest.textContent = `最新Plan: ${planId}（${summary.status || "COMPLETED"}）`;
      form.hidden = true;
    } catch (error) {
      setMessage(error.message || "Cleanup処理に失敗しました", "error");
      updateSubmitState();
    }
  });

  updateSubmitState();
})();
