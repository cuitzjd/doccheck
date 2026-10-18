/* 文稿检查系统前端逻辑（无外部依赖） */
(function () {
  "use strict";

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function setMsg(el, text, isErr) {
    if (!el) return;
    el.textContent = text || "";
    el.className = "msg " + (isErr ? "error" : "ok");
  }
  function api(url, opts) {
    return fetch(url, opts).then(function (r) {
      if (!r.ok) {
        return r.json().then(function (j) {
          throw new Error(j.error || ("HTTP " + r.status));
        }, function () { throw new Error("HTTP " + r.status); });
      }
      return r.json();
    });
  }
  function postJSON(url, data) {
    return api(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data || {})
    });
  }
  /* 轮询任务直至完成；onTick(task) 每 500ms 调用 */
  function pollTask(taskId, onTick, interval) {
    interval = interval || 500;
    return new Promise(function (resolve, reject) {
      (function tick() {
        api("/api/progress?task_id=" + encodeURIComponent(taskId)).then(function (r) {
          var t = r.task;
          if (onTick) onTick(t);
          if (t.done) {
            if (t.error) reject(new Error(t.error)); else resolve(t.result);
          } else setTimeout(tick, interval);
        }, function (e) { reject(e); });
      })();
    });
  }
  function dlLink(path, name) {
    return '<a class="dl" href="/api/download?path=' + encodeURIComponent(path) +
      '">下载 ' + esc(name || path) + "</a>";
  }

  /* ============ 页面1 ============ */
  function initPage1() {
    var uploadMsg = $("#uploadMsg"), extractMsg = $("#extractMsg"), confirmMsg = $("#confirmMsg");

    function renderFiles(files) {
      var tb = $("#fileTable tbody");
      tb.innerHTML = files.map(function (f) {
        return "<tr><td>" + esc(f.name) + "</td><td>" + esc(f.doc_type) + "</td><td>" +
          esc(f.fmt) + "</td><td>" + esc(f.size_h) + "</td><td>" +
          '<button class="btn btn-sm" data-del="' + esc(f.path) + '">删除</button></td></tr>';
      }).join("") || '<tr><td colspan="5" class="hint">尚未导入文件</td></tr>';
      $all("[data-del]", tb).forEach(function (b) {
        b.onclick = function () {
          postJSON("/api/files/delete", { path: b.getAttribute("data-del") }).then(function (r) {
            renderFiles(r.files); setMsg(uploadMsg, "已删除");
          }, function (e) { setMsg(uploadMsg, e.message, true); });
        };
      });
    }

    function addListRow(listName, value) {
      var box = $('[data-list="' + listName + '"]');
      var div = document.createElement("div");
      div.className = "row";
      div.innerHTML = '<input type="text" class="input-full" value="' + esc(value || "") +
        '"> <button class="btn btn-sm">删除</button>';
      div.querySelector("button").onclick = function () { div.remove(); };
      box.appendChild(div);
    }
    $all("[data-add]").forEach(function (b) {
      b.onclick = function () { addListRow(b.getAttribute("data-add")); };
    });

    function fillProfile(p, confidence) {
      $all("[data-field]").forEach(function (inp) {
        inp.value = p[inp.getAttribute("data-field")] || "";
      });
      ["contractors", "payment_terms"].forEach(function (k) {
        $('[data-list="' + k + '"]').innerHTML = "";
        (p[k] && p[k].length ? p[k] : [""]).forEach(function (v) { addListRow(k, v); });
      });
      // 低置信字段高亮 + 依据片段
      $all("#contractTable tr").forEach(function (tr) { tr.classList.remove("low-conf"); });
      $all(".conf-note").forEach(function (n) { n.remove(); });
      Object.keys(confidence || {}).forEach(function (field) {
        var inp = $('[data-field="' + field + '"]');
        var host = inp ? inp.closest("td") : $('[data-list="' + field + '"]');
        if (!host) return;
        host.closest("tr").classList.add("low-conf");
        var note = document.createElement("div");
        note.className = "conf-note";
        note.textContent = "低置信，依据片段：" + confidence[field];
        host.appendChild(note);
      });
    }

    function loadState() {
      api("/api/state").then(function (s) {
        renderFiles(s.files);
        if (s.contract && s.confirmed) fillProfile(s.contract, {});
        var tl = s.timeline || {};
        $("#tl_signed").value = tl.contract_signed || "";
        $("#tl_workorder").value = tl.work_order || "";
        $("#tl_online").value = tl.system_online || "";
        if (s.confirmed) setMsg(confirmMsg, "基准信息已确认，可进入页面2。");
      });
    }
    loadState();

    $("#uploadBtn").onclick = function () {
      var files = $("#uploadInput").files;
      if (!files.length) { setMsg(uploadMsg, "请先选择文件", true); return; }
      var fd = new FormData();
      for (var i = 0; i < files.length; i++) fd.append("files", files[i]);
      setMsg(uploadMsg, "上传中…");
      api("/api/upload", { method: "POST", body: fd }).then(function (r) {
        renderFiles(r.files); setMsg(uploadMsg, "已上传 " + r.saved.length + " 个文件");
      }, function (e) { setMsg(uploadMsg, e.message, true); });
    };

    $("#importFolderBtn").onclick = function () {
      var p = $("#folderPath").value.trim();
      if (!p) { setMsg(uploadMsg, "请输入文件夹路径", true); return; }
      postJSON("/api/import_folder", { path: p }).then(function (r) {
        renderFiles(r.files); setMsg(uploadMsg, "已导入 " + r.added + " 个文件");
      }, function (e) { setMsg(uploadMsg, e.message, true); });
    };

    $("#extractBtn").onclick = function () {
      setMsg(extractMsg, "提交抽取任务…");
      postJSON("/api/extract_contract", {}).then(function (r) {
        pollTask(r.task_id, function (t) { setMsg(extractMsg, t.label || "抽取中…"); }).then(function (res) {
          var p = res.profile || {};
          fillProfile(p, p.confidence || {});
          setMsg(extractMsg, "抽取完成，请逐项核对后点击「确认基准信息」。" +
            (p.needs_review ? "（含低置信字段，已黄色标出）" : ""));
        }, function (e) {
          setMsg(extractMsg, "抽取失败：" + e.message + "。可手工录入下方各字段。", true);
        });
      }, function (e) { setMsg(extractMsg, e.message, true); });
    };

    $("#confirmBtn").onclick = function () {
      var p = {};
      $all("[data-field]").forEach(function (inp) {
        p[inp.getAttribute("data-field")] = inp.value;
      });
      ["contractors", "payment_terms"].forEach(function (k) {
        p[k] = $all('[data-list="' + k + '"] input').map(function (i) { return i.value; })
          .filter(function (v) { return v.trim(); });
      });
      var tl = {
        contract_signed: $("#tl_signed").value,
        work_order: $("#tl_workorder").value,
        system_online: $("#tl_online").value
      };
      postJSON("/api/confirm_contract", { profile: p, timeline: tl }).then(function () {
        setMsg(confirmMsg, "已确认并写入配置，正在进入检查页…");
        setTimeout(function () { location.href = "/check"; }, 600);
      }, function (e) { setMsg(confirmMsg, e.message, true); });
    };
  }

  /* ============ 页面2 ============ */
  function initPage2() {
    var checkMsg = $("#checkMsg"), fixMsg = $("#fixMsg");
    var issues = [];
    var PAGE_SIZE = 50;
    var shownCounts = {};   // 规则组 key -> 已渲染条数
    var SEV_ORDER = { "高": 0, "中": 1, "低": 2 };
    var sortMode = "prob_desc";

    function stats() {
      var c = { "高": 0, "中": 0, "低": 0 };
      issues.forEach(function (i) { c[i.severity] = (c[i.severity] || 0) + 1; });
      var pending = issues.filter(function (i) { return i.status === "待判定"; }).length;
      var highProb = issues.filter(function (i) { return Number(i.error_probability) >= 0.8; }).length;
      $("#statBar").innerHTML =
        '<span class="stat-pill">共 ' + issues.length + ' 条（待判定 ' + pending + '）</span>' +
        '<span class="stat-pill"><span class="badge badge-高">高</span> ' + (c["高"] || 0) + '</span>' +
        '<span class="stat-pill"><span class="badge badge-中">中</span> ' + (c["中"] || 0) + '</span>' +
        '<span class="stat-pill"><span class="badge badge-低">低</span> ' + (c["低"] || 0) + '</span>' +
        '<span class="stat-pill">高概率（≥80%） ' + highProb + '</span>';
    }

    function sevBadges(arr) {
      var c = { "高": 0, "中": 0, "低": 0 };
      arr.forEach(function (i) { c[i.severity] = (c[i.severity] || 0) + 1; });
      return '<span class="badge badge-高">' + (c["高"] || 0) + '</span> ' +
        '<span class="badge badge-中">' + (c["中"] || 0) + '</span> ' +
        '<span class="badge badge-低">' + (c["低"] || 0) + '</span>';
    }

    function judgeBtns(it) {
      function cls(v, sel) { return it.status === v ? sel : ""; }
      return '<div class="judge-btns" data-id="' + esc(it.id) + '">' +
        '<button data-st="是问题" class="' + cls("是问题", "sel-yes") + '">是问题</button>' +
        '<button data-st="误报" class="' + cls("误报", "sel-no") + '">误报</button>' +
        '<button data-st="忽略" class="' + cls("忽略", "sel-ign") + '">忽略</button></div>';
    }

    function issueCard(it) {
      var probability = Number(it.error_probability);
      if (!isFinite(probability)) probability = 0.5;
      probability = Math.max(0, Math.min(1, probability));
      var probabilityPct = Math.round(probability * 100);
      var probClass = probability >= 0.8 ? "prob-high" : probability >= 0.5 ? "prob-mid" : "prob-low";
      return '<div class="issue-card"><div class="head">' +
        '<span class="rule">' + esc(it.rule_id) + " " + esc(it.rule_name) + "</span>" +
        '<span class="badge badge-' + esc(it.severity) + '">' + esc(it.severity) + "</span>" +
        '<span class="probability ' + probClass + '">实际错误概率 ' + probabilityPct + '% · ' +
          esc(it.probability_source === "jev" ? "Jev Noul" : "本地上下文") + '</span>' +
        '<span class="meta">' + esc(it.doc_name) + " · " + esc(it.location) +
        (it.fixable ? " · 可自动修复" : " · 需人工") + "</span>" +
        '<button class="btn btn-sm ctx-btn" data-ctx="' + esc(it.id) + '">上下文</button></div>' +
        '<dl class="body">' +
        "<dt>原文片段</dt><dd>" + esc(it.original) + "</dd>" +
        "<dt>基准值</dt><dd>" + esc(it.expected || "—") + "</dd>" +
        "<dt>修改建议</dt><dd>" + esc(it.suggestion || "—") + "</dd>" +
        "<dt>概率依据</dt><dd>" + esc(it.probability_reason || "—") + "</dd></dl>" +
        judgeBtns(it) + "</div>";
    }

    function probabilityValue(inputId, fallback) {
      var raw = $(inputId).value.trim();
      if (!raw) return fallback;
      var value = Number(raw) / 100;
      return isFinite(value) ? Math.max(0, Math.min(1, value)) : fallback;
    }

    function syncBulkOptions() {
      var cat = $("#bulkCategory").value;
      var rule = $("#bulkRule").value;
      var cats = Array.from(new Set(issues.map(function (i) { return i.category; }).filter(Boolean))).sort();
      var rules = issues.map(function (i) { return { id: i.rule_id, name: i.rule_name }; });
      var seen = {};
      rules = rules.filter(function (i) { if (seen[i.id]) return false; seen[i.id] = true; return true; })
        .sort(function (a, b) { return a.id < b.id ? -1 : 1; });
      $("#bulkCategory").innerHTML = '<option value="">全部</option>' +
        cats.map(function (v) { return '<option value="' + esc(v) + '">' + esc(v) + '</option>'; }).join("");
      $("#bulkRule").innerHTML = '<option value="">全部</option>' +
        rules.map(function (v) { return '<option value="' + esc(v.id) + '">' + esc(v.id + " " + v.name) + '</option>'; }).join("");
      if (cats.indexOf(cat) >= 0) $("#bulkCategory").value = cat;
      if (rules.some(function (v) { return v.id === rule; })) $("#bulkRule").value = rule;
    }

    function currentFilters() {
      return {
        category: $("#bulkCategory").value,
        rule_id: $("#bulkRule").value,
        min_probability: probabilityValue("#bulkMinProb", 0),
        max_probability: probabilityValue("#bulkMaxProb", 1)
      };
    }

    function matches(it, filters) {
      var probability = Number(it.error_probability);
      if (!isFinite(probability)) probability = 0.5;
      return (!filters.category || it.category === filters.category) &&
        (!filters.rule_id || it.rule_id === filters.rule_id) &&
        probability >= filters.min_probability && probability <= filters.max_probability;
    }

    function renderRuleIssues(box, key) {
      var arr = box._issues;
      var shown = shownCounts[key] || PAGE_SIZE;
      box.innerHTML = arr.slice(0, shown).map(issueCard).join("") +
        (arr.length > shown
          ? '<div class="issue-card"><button class="btn btn-sm" data-more="' + esc(key) +
            '">加载更多（剩余 ' + (arr.length - shown) + ' 条）</button></div>'
          : "");
    }

    function issueProbability(it) {
      var probability = Number(it.error_probability);
      return isFinite(probability) ? Math.max(0, Math.min(1, probability)) : 0.5;
    }

    function compareIssues(a, b) {
      var result = 0;
      if (sortMode === "prob_asc") {
        result = issueProbability(a) - issueProbability(b);
      } else if (sortMode === "severity") {
        result = (SEV_ORDER[a.severity] - SEV_ORDER[b.severity]) ||
          (issueProbability(b) - issueProbability(a));
      } else if (sortMode === "category") {
        result = (a.category || "").localeCompare(b.category || "") ||
          (a.rule_id || "").localeCompare(b.rule_id || "");
      } else if (sortMode === "rule") {
        result = (a.rule_id || "").localeCompare(b.rule_id || "") ||
          (a.rule_name || "").localeCompare(b.rule_name || "");
      } else if (sortMode === "document") {
        result = (a.doc_name || "").localeCompare(b.doc_name || "") ||
          (a.location || "").localeCompare(b.location || "");
      } else {
        result = issueProbability(b) - issueProbability(a);
      }
      return result || (SEV_ORDER[a.severity] - SEV_ORDER[b.severity]) ||
        (a.rule_id < b.rule_id ? -1 : a.rule_id > b.rule_id ? 1 : 0) ||
        (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
    }

    function maxProbability(items) {
      return items.reduce(function (max, item) {
        return Math.max(max, issueProbability(item));
      }, 0);
    }

    function render() {
      stats();
      syncBulkOptions();
      var filters = currentFilters();
      var highOnly = $("#highOnly").checked;
      var list = (highOnly ? issues.filter(function (i) { return i.severity === "高"; })
                           : issues.slice());
      list = list.filter(function (i) { return matches(i, filters); });
      list.sort(compareIssues);
      var cats = {};   // category -> ruleKey -> {rule_id, rule_name, issues: []}
      list.forEach(function (i) {
        var c = cats[i.category] = cats[i.category] || {};
        var k = i.rule_id;
        (c[k] = c[k] || { rule_id: i.rule_id, rule_name: i.rule_name, issues: [] }).issues.push(i);
      });
      var categoryKeys = Object.keys(cats).sort(function (a, b) {
        var aIssues = Object.keys(cats[a]).reduce(function (items, key) {
          return items.concat(cats[a][key].issues);
        }, []);
        var bIssues = Object.keys(cats[b]).reduce(function (items, key) {
          return items.concat(cats[b][key].issues);
        }, []);
        return compareIssues(aIssues[0], bIssues[0]) || (a < b ? -1 : a > b ? 1 : 0);
      });
      var html = categoryKeys.map(function (cat) {
        var catIssues = [];
        Object.keys(cats[cat]).forEach(function (k) {
          catIssues = catIssues.concat(cats[cat][k].issues);
        });
        var rulesHtml = Object.keys(cats[cat]).sort(function (a, b) {
          return compareIssues(cats[cat][a].issues[0], cats[cat][b].issues[0]) ||
            (a < b ? -1 : a > b ? 1 : 0);
        }).map(function (k) {
          var g = cats[cat][k];
          var key = cat + "|" + k;
          return '<details class="rule-group"' + (highOnly ? " open" : "") + '><summary>' +
            '<span class="rule">' + esc(g.rule_id) + " " + esc(g.rule_name) + "</span>" +
            '<span class="meta">' + g.issues.length + " 条</span> " + sevBadges(g.issues) +
            ' <button class="btn btn-sm" data-batch data-rule="' + esc(g.rule_id) +
            '" data-st="是问题">本组全部：是问题</button>' +
            '<button class="btn btn-sm" data-batch data-rule="' + esc(g.rule_id) +
            '" data-st="误报">本组全部：误报</button></summary>' +
            '<div class="rule-issues" data-key="' + esc(key) + '"></div></details>';
        }).join("");
        return '<details class="issue-group"' + (highOnly ? " open" : "") + '><summary>' +
          '<span class="cat-name">' + esc(cat) + "</span>" +
          '<span class="meta">' + catIssues.length + " 条</span> " + sevBadges(catIssues) +
          ' <button class="btn btn-sm" data-batch data-cat="' + esc(cat) +
          '" data-st="是问题">本类全部：是问题</button>' +
          '<button class="btn btn-sm" data-batch data-cat="' + esc(cat) +
          '" data-st="误报">本类全部：误报</button></summary>' +
          rulesHtml + "</details>";
      }).join("");
      $("#issueList").innerHTML = html || '<p class="hint">无符合筛选条件的问题。</p>';
      $all(".rule-issues").forEach(function (box) {
        var key = box.getAttribute("data-key");
        var cat = key.split("|")[0], rk = key.split("|").slice(1).join("|");
        box._issues = (cats[cat] && cats[cat][rk]) ? cats[cat][rk].issues : [];
        renderRuleIssues(box, key);
      });
      $("#issuePanel").style.display = issues.length ? "" : "none";
      $("#fixPanel").style.display = issues.length ? "" : "none";
      renderManual();
    }

    /* 单条判定后仅就地更新按钮状态，避免整树重绘 */
    function refreshJudgeStates() {
      var byId = {};
      issues.forEach(function (i) { byId[i.id] = i; });
      $all(".judge-btns[data-id]").forEach(function (box) {
        var it = byId[box.getAttribute("data-id")];
        if (!it) return;
        $all("button", box).forEach(function (b) {
          var st = b.getAttribute("data-st");
          b.className = it.status === st
            ? (st === "是问题" ? "sel-yes" : st === "误报" ? "sel-no" : "sel-ign") : "";
        });
      });
      stats();
      renderManual();
    }

    function markOne(id, st, onDone) {
      var it = issues.find(function (x) { return x.id === id; });
      if (it && it.status === st) st = "待判定"; // 再次点击取消
      postJSON("/api/issues/mark", { id: id, status: st }).then(function () {
        if (it) it.status = st;
        refreshJudgeStates();
        if (onDone) onDone(st);
      });
    }

    function markBatch(st, cat, rule, filters) {
      var payload = { status: st };
      if (cat) payload.category = cat;
      if (rule) payload.rule_id = rule;
      filters = filters || {};
      if (filters.min_probability > 0) payload.min_probability = filters.min_probability;
      if (filters.max_probability < 1) payload.max_probability = filters.max_probability;
      if ($("#pendingOnly").checked) payload.only_status = "待判定";
      postJSON("/api/issues/mark_all", payload).then(function (r) {
        issues.forEach(function (i) {
          if (cat && i.category !== cat) return;
          if (rule && i.rule_id !== rule) return;
          if (payload.only_status && i.status !== payload.only_status) return;
          if (filters && !matches(i, filters)) return;
          i.status = st;
        });
        refreshJudgeStates();
        setMsg(checkMsg, "已批量更新 " + r.updated + " 条");
      });
    }

    /* 事件委托：判定 / 上下文 / 加载更多 / 批量 */
    $("#issueList").addEventListener("click", function (e) {
      var b = e.target.closest("button");
      if (!b) return;
      if (b.hasAttribute("data-batch")) {
        e.preventDefault(); e.stopPropagation();
        markBatch(b.getAttribute("data-st"), b.getAttribute("data-cat"),
                  b.getAttribute("data-rule"), currentFilters());
        return;
      }
      if (b.hasAttribute("data-more")) {
        var key = b.getAttribute("data-more");
        shownCounts[key] = (shownCounts[key] || PAGE_SIZE) + PAGE_SIZE;
        var box = $('.rule-issues[data-key="' + key + '"]');
        if (box) renderRuleIssues(box, key);
        return;
      }
      if (b.hasAttribute("data-ctx")) {
        openCtx(b.getAttribute("data-ctx"));
        return;
      }
      if (b.hasAttribute("data-st") && b.parentElement.hasAttribute("data-id")) {
        markOne(b.parentElement.getAttribute("data-id"), b.getAttribute("data-st"));
      }
    });

    /* ---- 上下文 modal ---- */
    var ctxId = null;
    function closeCtx() { ctxId = null; $("#ctxModal").style.display = "none"; }
    function openCtx(id) {
      var it = issues.find(function (x) { return x.id === id; });
      if (!it) return;
      ctxId = id;
      $("#ctxTitle").textContent = it.rule_id + " " + it.rule_name;
      $("#ctxInfo").innerHTML =
        "<dt>文档定位</dt><dd>" + esc(it.doc_name) + " · " + esc(it.location) + "</dd>" +
        "<dt>原文片段</dt><dd>" + esc(it.original) + "</dd>" +
        "<dt>基准值</dt><dd>" + esc(it.expected || "—") + "</dd>" +
        "<dt>修改建议</dt><dd>" + esc(it.suggestion || "—") + "</dd>";
      $("#ctxContent").innerHTML = '<span class="hint">上下文加载中…</span>';
      $("#ctxModal").style.display = "";
      $all("#ctxJudge button").forEach(function (b) {
        b.className = it.status === b.getAttribute("data-st")
          ? (b.getAttribute("data-st") === "是问题" ? "sel-yes"
             : b.getAttribute("data-st") === "误报" ? "sel-no" : "sel-ign") : "";
      });
      api("/api/issues/context?id=" + encodeURIComponent(id)).then(function (c) {
        if (ctxId !== id) return;  // 已切换/关闭
        if (c.type === "image") {
          $("#ctxContent").innerHTML =
            (c.found === false ? '<div class="hint">未定位到原句，显示整页</div>' : "") +
            '<img src="' + c.url + "?t=" + Date.now() + '" alt="上下文截图">';
        } else if (c.type === "text") {
          $("#ctxContent").innerHTML =
            (c.note ? '<div class="hint">' + esc(c.note) + "</div>" : "") +
            (c.paragraphs || []).map(function (p) {
              return '<p class="ctx-para' + (p.hit ? " ctx-hit" : "") + '">' +
                esc(p.text || "（空段）") + "</p>";
            }).join("");
        } else {
          $("#ctxContent").innerHTML = '<div class="error">' +
            esc(c.msg || "上下文获取失败") + "</div>";
        }
      }, function (e) {
        if (ctxId === id) $("#ctxContent").innerHTML =
          '<div class="error">' + esc(e.message) + "</div>";
      });
    }
    $("#ctxClose").onclick = closeCtx;
    $("#ctxModal").addEventListener("click", function (e) {
      if (e.target === this) closeCtx();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && ctxId) closeCtx();
    });
    $all("#ctxJudge button").forEach(function (b) {
      b.onclick = function () {
        if (!ctxId) return;
        markOne(ctxId, b.getAttribute("data-st"), function () {
          var next = issues.find(function (x) { return x.status === "待判定"; });
          if (next) openCtx(next.id);
          else {
            $("#ctxContent").innerHTML = '<div class="msg ok">全部问题已判定完成。</div>';
            setTimeout(closeCtx, 1200);
          }
        });
      };
    });

    function renderManual() {
      var manual = issues.filter(function (i) { return i.status === "是问题" && !i.fixable; });
      $("#manualList").innerHTML = manual.length ?
        '<div class="error">以下 ' + manual.length +
        ' 条已确认问题不可自动修复，需人工处理：<ul>' +
        manual.map(function (i) {
          return "<li>" + esc(i.id) + " " + esc(i.rule_name) + "（" + esc(i.doc_name) + "）</li>";
        }).join("") + "</ul></div>" : "";
    }

    function loadIssues() {
      api("/api/issues").then(function (r) {
        issues = r.issues || [];
        shownCounts = {};
        render();
      });
    }
    loadIssues();

    $("#highOnly").onchange = render;
    $("#sortMode").onchange = function () {
      sortMode = this.value || "prob_desc";
      shownCounts = {};
      render();
    };
    ["#bulkCategory", "#bulkRule", "#bulkMinProb", "#bulkMaxProb"].forEach(function (sel) {
      $(sel).onchange = render;
    });
    [["#bulkYesBtn", "是问题"], ["#bulkNoBtn", "误报"], ["#bulkIgnoreBtn", "忽略"]]
      .forEach(function (entry) {
        $(entry[0]).onclick = function () {
          var filters = currentFilters();
          var visible = issues.filter(function (i) {
            return (!$("#highOnly").checked || i.severity === "高") && matches(i, filters) &&
              (!$("#pendingOnly").checked || i.status === "待判定");
          }).length;
          if (!visible) { setMsg(checkMsg, "当前筛选条件下没有可批量处理的问题。", true); return; }
          if (!confirm("将批量更新 " + visible + " 条问题为“" + entry[1] + "”，确认继续？")) return;
          markBatch(entry[1], filters.category, filters.rule_id, filters);
        };
      });
    $("#pendingOnly").onchange = function () { render(); };

    $("#startCheckBtn").onclick = function () {
      $("#progressWrap").style.display = "";
      setMsg(checkMsg, "");
      postJSON("/api/check/start", {}).then(function (r) {
        pollTask(r.task_id, function (t) {
          var pct = t.total ? Math.round(100 * t.progress / t.total) : 0;
          $("#progressFill").style.width = pct + "%";
          $("#progressPct").textContent = pct + "%";
          $("#progressLabel").textContent = t.label || "";
          $("#progressCount").textContent = t.total ? ("已完成 " + t.progress + "/" + t.total) : "";
        }).then(function (res) {
          setMsg(checkMsg, "检查完成，共发现 " + (res.count || 0) + " 条问题。");
          loadIssues();
        }, function (e) { setMsg(checkMsg, "检查失败：" + e.message, true); });
      }, function (e) { setMsg(checkMsg, e.message, true); });
    };

    $("#fixBtn").onclick = function () {
      var n = issues.filter(function (i) { return i.status === "是问题" && i.fixable; }).length;
      if (!n) { setMsg(fixMsg, "没有可自动修复的确认项（需状态=是问题且可自动修复）。", true); return; }
      if (!confirm("将自动修复 " + n + " 条问题，原件会备份。确认继续？")) return;
      setMsg(fixMsg, "修复任务已提交…");
      postJSON("/api/fix", {}).then(function (r) {
        pollTask(r.task_id, function (t) { setMsg(fixMsg, t.label || "修复中…"); }).then(function () {
          location.href = "/result";
        }, function (e) { setMsg(fixMsg, "修复失败：" + e.message, true); });
      }, function (e) { setMsg(fixMsg, e.message, true); });
    };
  }

  /* ============ 页面3 ============ */
  function initPage3() {
    var opMsg = $("#opMsg");
    api("/api/fix_result").then(function (r) {
      var fr = r.fix_result;
      if (!fr) { $("#emptyPanel").style.display = ""; return; }
      $("#resultPanel").style.display = "";
      $("#fixTime").textContent = "修复时间：" + (fr.time || "");
      var items = fr.items || [];
      var c = { "成功": 0, "失败": 0, "跳过": 0 };
      items.forEach(function (i) { c[i.status] = (c[i.status] || 0) + 1; });
      $("#fixStat").innerHTML =
        '<span class="stat-pill result-ok">成功 ' + (c["成功"] || 0) + "</span>" +
        '<span class="stat-pill result-fail">失败 ' + (c["失败"] || 0) + "</span>" +
        '<span class="stat-pill result-skip">跳过 ' + (c["跳过"] || 0) + "</span>" +
        '<span class="stat-pill">需人工 ' + ((fr.manual || []).length) + "</span>";
      $("#fixTable tbody").innerHTML = items.map(function (i) {
        return "<tr><td>" + esc(i.issue_id || "—") + '</td><td class="fix-status-' +
          esc(i.status) + '">' + esc(i.status) + "</td><td>" + esc(i.reason || "") +
          "</td><td>" + esc(i.fix_detail || "") + "</td><td>" +
          (i.backup ? dlLink(i.backup, "备份文件") : "") + "</td></tr>";
      }).join("") || '<tr><td colspan="5" class="hint">无明细</td></tr>';
      var manual = fr.manual || [];
      if (manual.length) {
        $("#manualCard").style.display = "";
        $("#manualTable tbody").innerHTML = manual.map(function (i) {
          return "<tr><td>" + esc(i.id) + "</td><td>" + esc(i.rule_id) + " " + esc(i.rule_name) +
            "</td><td>" + esc(i.doc_name) + "</td><td>" + esc(i.location) + "</td><td>" +
            esc(i.suggestion) + "</td></tr>";
        }).join("");
      }
      var outs = fr.outputs || [];
      $("#downloadLinks").innerHTML = outs.length ?
        outs.map(function (o) { return dlLink(o, "修复后文档：" + o.split("/").pop()); }).join(" ") :
        '<span class="hint">无修复输出文件</span>';
    });

    function doExport(fmt) {
      postJSON("/api/tools/export", { fmt: fmt }).then(function (r) {
        setMsg(opMsg, "导出成功");
        $("#opMsg").insertAdjacentHTML("afterend",
          '<div class="msg ok">' + dlLink(r.out, r.name) + "</div>");
      }, function (e) { setMsg(opMsg, e.message, true); });
    }
    $("#exportExcelBtn").onclick = function () { doExport("excel"); };
    $("#exportJsonBtn").onclick = function () { doExport("json"); };
    $("#rollbackBtn").onclick = function () {
      if (!confirm("确认回滚到修复前状态？")) return;
      postJSON("/api/rollback", {}).then(function () {
        setMsg(opMsg, "已回滚。");
      }, function (e) { setMsg(opMsg, e.message, true); });
    };
  }

  /* ============ 页面4 ============ */
  function initPage4() {
    /* --- 署名 --- */
    var stampMsg = $("#stampMsg"), stampPdfs = [];
    $("#stampPos").onchange = function () {
      $("#stampXY").style.display = this.value === "custom" ? "" : "none";
    };
    $("#stampUploadBtn").onclick = function () {
      var files = $("#stampFiles").files;
      if (!files.length) { setMsg(stampMsg, "请先选择 PDF 文件", true); return; }
      var fd = new FormData();
      for (var i = 0; i < files.length; i++) fd.append("files", files[i]);
      api("/api/tools/upload", { method: "POST", body: fd }).then(function (r) {
        stampPdfs = r.files;
        $("#stampFileList").innerHTML = r.files.map(function (f) {
          return '<span class="chip">' + esc(f.name) + "（" + esc(f.size_h) + "）</span>";
        }).join("");
        setMsg(stampMsg, "已上传 " + r.files.length + " 个 PDF");
      }, function (e) { setMsg(stampMsg, e.message, true); });
    };
    function stampPayload() {
      return {
        pdf_paths: stampPdfs.map(function (f) { return f.path; }),
        lines: $("#stampLines").value.split("\n").map(function (s) { return s.trim(); })
          .filter(function (s) { return s; }),
        position: $("#stampPos").value,
        font_size: parseInt($("#stampSize").value, 10) || 14,
        x: parseInt($("#stampX").value, 10), y: parseInt($("#stampY").value, 10)
      };
    }
    $("#stampPreviewBtn").onclick = function () {
      var p = stampPayload();
      if (!p.pdf_paths.length) { setMsg(stampMsg, "请先上传 PDF", true); return; }
      p.pdf_path = p.pdf_paths[0];
      fetch("/api/tools/stamp/preview", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(p)
      }).then(function (r) {
        if (!r.ok) return r.json().then(function (j) { throw new Error(j.error || "预览失败"); });
        return r.blob();
      }).then(function (blob) {
        $("#stampPreview").innerHTML = '<img src="' + URL.createObjectURL(blob) + '">';
        setMsg(stampMsg, "预览生成（取第一份 PDF 首页）");
      }, function (e) { setMsg(stampMsg, e.message, true); });
    };
    $("#stampApplyBtn").onclick = function () {
      var p = stampPayload();
      postJSON("/api/tools/stamp/apply", p).then(function (r) {
        pollTask(r.task_id, function (t) { setMsg(stampMsg, t.label || "处理中…"); }).then(function (res) {
          setMsg(stampMsg, "处理完成");
          $("#stampResult").innerHTML = "<ul>" + res.results.map(function (x) {
            var name = x.pdf.split("/").pop();
            if (x.skipped) return '<li class="result-skip">' + esc(name) + "：" + esc(x.msg) + "</li>";
            if (x.ok) return '<li class="result-ok">' + esc(name) + "：成功 " +
              (x.out ? dlLink(x.out, "下载") : "") + "</li>";
            return '<li class="result-fail">' + esc(name) + "：失败 " + esc(x.msg) + "</li>";
          }).join("") + "</ul>";
        }, function (e) { setMsg(stampMsg, e.message, true); });
      }, function (e) { setMsg(stampMsg, e.message, true); });
    };

    /* --- 格式转换 --- */
    var convMsg = $("#convMsg");
    $("#convMode").onchange = function () {
      $("#ocrBox").style.display = this.value === "pdf2word" ? "" : "none";
    };
    $("#convBtn").onclick = function () {
      var f = $("#convFile").files[0];
      if (!f) { setMsg(convMsg, "请先选择文件", true); return; }
      var fd = new FormData();
      fd.append("files", f);
      setMsg(convMsg, "上传中…");
      api("/api/tools/upload", { method: "POST", body: fd }).then(function (r) {
        var path = r.files[0].path;
        setMsg(convMsg, "转换中…");
        return postJSON("/api/tools/convert", {
          path: path, mode: $("#convMode").value, force_ocr: $("#forceOcr").checked
        });
      }).then(function (r) {
        return pollTask(r.task_id, function (t) { setMsg(convMsg, t.label || "转换中…"); }, 1000);
      }).then(function (res) {
        if (res.ok) {
          setMsg(convMsg, "转换成功" + (res.ocr ? "（OCR 模式）" : "") + "：" + (res.msg || ""));
          $("#convResult").html(res.out ? dlLink(res.out, res.out.split("/").pop()) : "");
        } else {
          setMsg(convMsg, "转换失败：" + (res.msg || "未知原因"), true);
        }
      }, function (e) { setMsg(convMsg, e.message, true); });
    };

    /* --- 指引册 --- */
    var guideMsg = $("#guideMsg");
    function genGuide(kind) {
      setMsg(guideMsg, "生成中…");
      postJSON("/api/tools/guide", { kind: kind }).then(function (r) {
        return pollTask(r.task_id, function (t) { setMsg(guideMsg, t.label || "生成中…"); }, 1000);
      }).then(function (res) {
        setMsg(guideMsg, "生成完成");
        $("#guideResult").html(dlLink(res.out, res.name));
      }, function (e) { setMsg(guideMsg, e.message, true); });
    }
    $("#guideDocBtn").onclick = function () { genGuide("doc"); };
    $("#guideBriefBtn").onclick = function () { genGuide("brief"); };

    /* --- 报告导出 --- */
    var exportMsg = $("#exportMsg");
    api("/api/state").then(function (s) {
      if (!s.has_issues) {
        $("#toolExportExcel").disabled = true;
        $("#toolExportJson").disabled = true;
        $("#exportHint").textContent = "暂无检查结果，请先在页面2执行检查。";
      }
    });
    function doExport(fmt) {
      postJSON("/api/tools/export", { fmt: fmt }).then(function (r) {
        setMsg(exportMsg, "导出成功");
        $("#exportResult").html(dlLink(r.out, r.name));
      }, function (e) { setMsg(exportMsg, e.message, true); });
    }
    $("#toolExportExcel").onclick = function () { doExport("excel"); };
    $("#toolExportJson").onclick = function () { doExport("json"); };
  }

  var page = document.body.getAttribute("data-page");
  if (page === "p1") initPage1();
  else if (page === "p2") initPage2();
  else if (page === "p3") initPage3();
  else if (page === "p4") initPage4();
})();
