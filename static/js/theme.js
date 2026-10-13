/* 描画前に外観を決める。作品の画像・原稿の色には適用しない。 */
(function () {
  "use strict";
  const root = document.documentElement;
  // 旧キーは初回のライトまで自動保存していたため、明示選択と区別できない。
  const key = "story-manga-appearance";
  const media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  const valid = function (value) { return ["system", "light", "dark"].includes(value) ? value : "system"; };
  let preference = "system";
  try { preference = valid(window.localStorage.getItem(key)); } catch (_) { /* 保存不可でも表示を続ける。 */ }

  function apply() {
    const dark = preference === "dark" || (preference === "system" && media && media.matches);
    root.dataset.theme = dark ? "dark" : "light";
    root.dataset.themePreference = preference;
    root.style.colorScheme = dark ? "dark" : "light";
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = dark ? "#161618" : "#f5f5f7";
    document.querySelectorAll("[data-theme-choice]").forEach(function (select) { select.value = preference; });
  }
  apply();
  if (media) {
    if (media.addEventListener) media.addEventListener("change", apply);
    else if (media.addListener) media.addListener(apply);
  }
  window.addEventListener("storage", function (event) {
    if (event.key === key || event.key === null) { preference = valid(event.newValue); apply(); }
  });
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
      const label = document.createElement("label");
      label.className = "theme-picker" + (button.classList.contains("theme-nav") ? " theme-nav" : "");
      const caption = document.createElement("span");
      caption.textContent = "外観";
      const select = document.createElement("select");
      select.setAttribute("data-theme-choice", "");
      select.setAttribute("aria-label", "外観モード");
      [["system", "自動"], ["light", "ライト"], ["dark", "ダーク"]].forEach(function (entry) {
        const option = document.createElement("option");
        option.value = entry[0]; option.textContent = entry[1]; select.appendChild(option);
      });
      select.addEventListener("change", function () {
        preference = valid(select.value);
        try { window.localStorage.setItem(key, preference); } catch (_) { /* この画面では選択を保持。 */ }
        apply();
      });
      label.appendChild(caption); label.appendChild(select); button.replaceWith(label);
    });
    apply();
  });
})();
