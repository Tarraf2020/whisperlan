// Single orchestrated moment: the hero terminal boots and plays a
// whisperlan session once on load. Replay is user-triggered. With
// prefers-reduced-motion the full transcript renders instantly.
(function () {
  "use strict";

  var screen = document.getElementById("screen");
  var replayBtn = document.getElementById("replay");

  // [class, text, kind] — kind "type" simulates keystrokes, "print" appears whole
  var script = [
    ["", "$ whisper --name ali", "type"],
    ["sys", "── 🤫 welcome to whisper. softly shouting HELLO to the LAN… ──", "print"],
    ["sys", "── cara joined from 192.168.1.42 🔒 ──", "print"],
    ["", "12:01 bob            present 🫡", "print"],
    ["", "12:01 ali            yo who is here??", "type"],
    ["", "12:02 cara           judging your snack choices", "print"],
    ["sys", "── 🔒 private with bob — type to whisper, /room to exit ──", "print"],
    ["priv", "🔒→bob secret plan at midnight", "type"],
    ["sys", "── 🔒 sent privately to bob ──", "print"],
    ["sys", "── 🔒 private message arrived — type to reply, /room to exit ──", "print"],
    ["", "12:03 bob            say less. bringing chips", "print"],
    ["me", "ali › you slipped away quietly. bye.", "print"]
  ];

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var timers = [];
  var cancelled = false;

  function later(fn, ms) {
    if (cancelled) return;
    timers.push(window.setTimeout(function () { if (!cancelled) fn(); }, ms));
  }

  function lineEl(cls) {
    var div = document.createElement("div");
    if (cls) div.className = cls;
    screen.appendChild(div);
    return div;
  }

  function renderInstant() {
    screen.innerHTML = "";
    script.forEach(function (entry) {
      var div = lineEl(entry[0]);
      div.textContent = entry[1];
    });
  }

  function play() {
    cancelled = true;
    timers.forEach(clearTimeout);
    timers = [];
    cancelled = false;
    screen.innerHTML = "";

    if (reduceMotion) { renderInstant(); return; }

    var t = 400;
    script.forEach(function (entry, i) {
      var cls = entry[0], text = entry[1], kind = entry[2];
      if (kind === "type") {
        later(function () {
          var div = lineEl(cls + " caret");
          var n = 0;
          var tick = window.setInterval(function () {
            if (cancelled) { window.clearInterval(tick); return; }
            n += 1;
            div.textContent = text.slice(0, n);
            if (n >= text.length) {
              window.clearInterval(tick);
              div.classList.remove("caret");
            }
          }, 34);
        }, t);
        t += text.length * 34 + 450;
      } else {
        later(function () { lineEl(cls).textContent = text; }, t);
        t += (i === 0 ? 500 : 750);
      }
    });
  }

  replayBtn.addEventListener("click", play);

  // copy buttons: label confirms what happened, then reverts
  document.querySelectorAll("[data-copy]").forEach(function (btn) {
    var original = btn.textContent;
    btn.addEventListener("click", function () {
      var done = function () {
        btn.textContent = "Copied to clipboard";
        window.setTimeout(function () { btn.textContent = original; }, 1800);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(btn.getAttribute("data-copy")).then(done, done);
      } else { done(); }
    });
  });

  // install tabs: user-triggered, shows what changed
  var tabs = Array.prototype.slice.call(document.querySelectorAll('[role="tab"]'));
  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      tabs.forEach(function (other) {
        var active = other === tab;
        other.setAttribute("aria-selected", active ? "true" : "false");
        document.getElementById("panel-" + other.getAttribute("data-tab")).hidden = !active;
      });
    });
  });

  play();
})();
