/* Presentation only: keep original inputs, event handlers, CSRF and configuration functions. */
function enhance() {
  for (const row of document.querySelectorAll("#jointRows .jointrow")) {
    if (row.dataset.mobileLabels) continue;
    row.dataset.mobileLabels = "1";
    const name = row.children[1]?.textContent || "关节";
    for (const [selector, caption] of [
      [".joint-offset", "零位补偿（°）"],
      [".joint-dir", "运动方向"],
    ]) {
      const input = row.querySelector(selector);
      if (!input) continue;
      input.setAttribute("aria-label", name + " " + caption);
      const label = document.createElement("label");
      label.className = "mobile-joint-label";
      const text = document.createElement("span");
      text.textContent = caption;
      input.before(label);
      label.append(text, input);
    }
  }
}
const parent = document.getElementById("jointRows");
if (parent) {
  new MutationObserver(enhance).observe(parent, { childList: true });
  enhance();
}
