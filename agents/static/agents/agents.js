// رفتار سمت مرورگرِ عامل‌ها: (۱) تازه‌سازی خودکار تا رسیدن پاسخ n8n  (۲) جعبه‌ی گفتگوی دستیار
(function () {
  "use strict";

  // ۱) تا وقتی نتیجه‌ی یک بررسی هنوز نرسیده، صفحه هر ۵ ثانیه تازه می‌شود (حداکثر حدود ۱۰ دقیقه؛ سپس سرور آن را «بی‌پاسخ» نشان می‌دهد)
  if (document.querySelector("[data-agent-waiting]")) {
    setTimeout(function () { location.reload(); }, 5000);
  }

  // ۲) جعبه‌ی گفتگو
  var panel = document.getElementById("assistant-panel");
  if (!panel) { return; }
  var toggle = document.getElementById("assistant-toggle");
  var log = document.getElementById("assistant-log");
  var form = document.getElementById("assistant-form");
  var input = document.getElementById("assistant-input");

  function setOpen(open) {
    panel.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
    if (open) { input.focus(); }
  }
  toggle.addEventListener("click", function () { setOpen(panel.hidden); });
  document.getElementById("assistant-close").addEventListener("click", function () { setOpen(false); });

  // همه‌ی متن‌ها با textContent درج می‌شوند تا خروجی مدل زبانی هرگز به‌عنوان HTML اجرا نشود
  function bubble(kind, text) {
    var el = document.createElement("div");
    el.className = "bubble " + kind;
    el.textContent = text;
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;
    return el;
  }

  function addSources(el, sources) {
    if (!sources || !sources.length) { return; }
    var details = document.createElement("details");
    var summary = document.createElement("summary");
    summary.textContent = "بندهای مرجع";
    details.appendChild(summary);
    sources.forEach(function (s) {
      var item = document.createElement("div");
      item.className = "source" + (s.cited ? " cited" : "");
      var head = document.createElement("strong");
      head.textContent = "[" + s.n + "] " + s.doc + (s.ref ? " — " + s.ref : "");
      var body = document.createElement("p");
      body.textContent = s.text;
      item.appendChild(head);
      item.appendChild(body);
      details.appendChild(item);
    });
    el.appendChild(details);
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var question = input.value.trim();
    if (!question) { return; }
    bubble("user", question);
    input.value = "";
    input.disabled = true;
    var waiting = bubble("bot waiting", "در حال جست‌وجو در آیین‌نامه…");
    fetch(form.dataset.url, {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-CSRFToken": form.dataset.csrf},
      body: JSON.stringify({question: question})
    }).then(function (response) {
      if (!response.ok) { throw new Error(response.status); }
      return response.json();
    }).then(function (data) {
      waiting.className = "bubble bot" + (data.ok ? "" : " error");
      waiting.textContent = data.ok ? data.answer : data.error;
      if (data.ok) { addSources(waiting, data.sources); }
    }).catch(function () {
      waiting.className = "bubble bot error";
      waiting.textContent = "ارتباط با سامانه برقرار نشد؛ دوباره تلاش کنید.";
    }).finally(function () {
      input.disabled = false;
      input.focus();
      log.scrollTop = log.scrollHeight;
    });
  });
})();
