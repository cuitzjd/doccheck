"""Flask 界面层：四页面向导 + 工具箱。

只依赖 doccheck.models / doccheck.config / doccheck.engine / doccheck.docloader；
contract_profile / repair / convert / pdfstamp / report / guide 等核心模块
在导入时 try/except 兜底，调用发生在用户操作触发的后台任务里，
缺失时在页面提示"核心模块尚未就绪"。
"""
import dataclasses
import datetime
import importlib
import inspect
import json
import os
import re
import sys
import threading
import traceback
import uuid

_WEBAPP_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_WEBAPP_DIR))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from flask import (Flask, Response, jsonify, redirect, render_template,
                   request, send_file, url_for)

from doccheck import config as config_mod
from doccheck.models import ContractProfile, Issue, IssueStatus, Severity

try:
    from doccheck import engine
except Exception:
    engine = None
try:
    from doccheck.docloader import detect_doc_type
except Exception:
    def detect_doc_type(filename):
        m = re.match(r"^【(?:\d+-)?([^】]+)】", filename)
        return m.group(1) if m else "未识别"


def _try_import(name):
    try:
        return importlib.import_module(name)
    except Exception:
        return None


contract_profile_mod = _try_import("doccheck.contract_profile")
repair_mod = _try_import("doccheck.repair")
convert_mod = _try_import("doccheck.convert")
pdfstamp_mod = _try_import("doccheck.pdfstamp")
report_mod = _try_import("doccheck.report")
guide_mod = _try_import("doccheck.guide")
context_mod = _try_import("doccheck.context")

CORE_MODULES = {
    "engine": engine,
    "contract_profile": contract_profile_mod,
    "repair": repair_mod,
    "convert": convert_mod,
    "pdfstamp": pdfstamp_mod,
    "report": report_mod,
    "guide": guide_mod,
}

# 可被测试覆盖的全局路径
WORKDIR = os.path.join(_PROJECT_ROOT, "workdir")
UPLOAD_DIR = os.path.join(WORKDIR, "uploads")
TOOLS_DIR = os.path.join(WORKDIR, "tools")
REPORT_DIR = os.path.join(WORKDIR, "reports")
SESSION_FILE = os.path.join(WORKDIR, "session.json")
CONFIG_PATH = config_mod.DEFAULT_PATH

ALLOWED_EXT = (".doc", ".docx", ".pdf")


def ensure_dirs():
    for d in (WORKDIR, UPLOAD_DIR, TOOLS_DIR, REPORT_DIR):
        os.makedirs(d, exist_ok=True)
    if context_mod is not None:
        context_mod.CONTEXT_DIR = os.path.join(WORKDIR, "context")
        os.makedirs(context_mod.CONTEXT_DIR, exist_ok=True)


# ---------------- 会话持久化 ----------------

_SESSION_LOCK = threading.Lock()
_EMPTY_SESSION = {"files": [], "issues": [], "checked": False, "fix_result": None}


def load_session():
    with _SESSION_LOCK:
        try:
            with open(SESSION_FILE, encoding="utf-8") as f:
                s = json.load(f)
            for k, v in _EMPTY_SESSION.items():
                s.setdefault(k, v)
            return s
        except Exception:
            return dict(_EMPTY_SESSION)


def save_session(s):
    with _SESSION_LOCK:
        tmp = SESSION_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=1)
        os.replace(tmp, SESSION_FILE)


# ---------------- 后台任务表 ----------------

TASKS = {}
_TASKS_LOCK = threading.Lock()


class CoreNotReady(Exception):
    pass


def need(mod, fname):
    """取核心模块函数；模块或函数缺失时抛 CoreNotReady。"""
    if mod is None or not hasattr(mod, fname):
        modname = getattr(mod, "__name__", None) or next(
            (k for k, v in CORE_MODULES.items() if v is mod), "?")
        raise CoreNotReady(f"核心模块尚未就绪（{modname}.{fname}）")
    return getattr(mod, fname)


def call_flex(fn, *args, **kwargs):
    """按函数实际签名过滤 kwargs，兼容核心侧签名差异。"""
    try:
        sig = inspect.signature(fn)
        params = sig.parameters
        if not any(p.kind == p.VAR_KEYWORD for p in params.values()):
            kwargs = {k: v for k, v in kwargs.items() if k in params}
    except (TypeError, ValueError):
        pass
    return fn(*args, **kwargs)


def new_task(label=""):
    tid = uuid.uuid4().hex[:12]
    with _TASKS_LOCK:
        TASKS[tid] = {"progress": 0, "total": 0, "label": label,
                      "result": None, "error": None, "done": False}
    return tid


def get_task(tid):
    with _TASKS_LOCK:
        return dict(TASKS.get(tid) or {})


def _update_task(tid, **kw):
    with _TASKS_LOCK:
        if tid in TASKS:
            TASKS[tid].update(kw)


def run_in_thread(tid, fn):
    def wrap():
        try:
            result = fn()
            _update_task(tid, result=result, done=True)
        except Exception as e:
            traceback.print_exc()
            _update_task(tid, error=str(e), done=True)
    threading.Thread(target=wrap, daemon=True).start()


# ---------------- 工具函数 ----------------

def is_confirmed():
    try:
        cfg = config_mod.load_config(CONFIG_PATH)
        return bool((cfg.get("contract") or {}).get("confirmed"))
    except Exception:
        return False


def issue_from_dict(d):
    fields = {f.name for f in dataclasses.fields(Issue)}
    kw = {k: v for k, v in d.items() if k in fields}
    kw["severity"] = Severity(kw.get("severity") or "低")
    kw["status"] = IssueStatus(kw.get("status") or "待判定")
    return Issue(**kw)


def obj_to_dict(o):
    if hasattr(o, "to_dict"):
        return o.to_dict()
    if isinstance(o, dict):
        return dict(o)
    if hasattr(o, "__dict__"):
        return dict(vars(o))
    return {"value": str(o)}


def fmt_size(n):
    try:
        n = int(n)
    except Exception:
        return "-"
    if n >= 1048576:
        return f"{n/1048576:.1f} MB"
    if n >= 1024:
        return f"{n/1024:.1f} KB"
    return f"{n} B"


def add_file_entry(path):
    name = os.path.basename(path)
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    entry = {"name": name, "path": path,
             "doc_type": detect_doc_type(name),
             "fmt": os.path.splitext(name)[1].lstrip(".").lower(),
             "size": size, "size_h": fmt_size(size)}
    s = load_session()
    s["files"] = [f for f in s["files"] if f["path"] != path and f["name"] != name]
    s["files"].append(entry)
    s["files"].sort(key=lambda f: f["name"])
    save_session(s)
    return entry


def _norm_convert_result(r):
    """convert 返回 (ok,msg,out) 或 str，统一成 dict。"""
    if isinstance(r, (tuple, list)):
        ok = bool(r[0]) if len(r) > 0 else False
        msg = str(r[1]) if len(r) > 1 else ""
        out = str(r[2]) if len(r) > 2 else ""
        return {"ok": ok, "msg": msg, "out": out}
    if isinstance(r, str):
        return {"ok": True, "msg": "完成", "out": r}
    return obj_to_dict(r)


def pdf_has_text(path):
    """粗判 PDF 是否有文本层（无则走 OCR）。"""
    try:
        with open(path, "rb") as f:
            data = f.read(3_000_000)
        return b"/Font" in data and (b"BT" in data or b"Tj" in data)
    except Exception:
        return False


def _allowed_download(path):
    if not path or not os.path.isfile(path):
        return False
    ap = os.path.abspath(path)
    for base in (WORKDIR, UPLOAD_DIR, TOOLS_DIR, REPORT_DIR):
        if ap.startswith(os.path.abspath(base) + os.sep):
            return True
    s = load_session()
    if ap in [os.path.abspath(f["path"]) for f in s["files"]]:
        return True
    fr = s.get("fix_result") or {}
    for key in ("outputs", "backups"):
        if ap in [os.path.abspath(p) for p in fr.get(key, []) if p]:
            return True
    return False


# ---------------- Flask 应用 ----------------

app = Flask(__name__,
            template_folder=os.path.join(_WEBAPP_DIR, "templates"),
            static_folder=os.path.join(_WEBAPP_DIR, "static"))


@app.context_processor
def inject_globals():
    missing = [k for k, v in CORE_MODULES.items() if v is None]
    return {"core_missing": missing, "contract_confirmed": is_confirmed()}


@app.route("/")
def page1():
    msg = request.args.get("msg", "")
    return render_template("page1.html", page="p1", msg=msg)


@app.route("/check")
def page2():
    if not is_confirmed():
        return redirect(url_for("page1", msg="请先在页面1确认合同基准信息，再进入检查。"))
    return render_template("page2.html", page="p2")


@app.route("/result")
def page3():
    return render_template("page3.html", page="p3")


@app.route("/tools")
def page4():
    return render_template("page4.html", page="p4")


# ---------------- 状态与文件 ----------------

@app.get("/api/state")
def api_state():
    s = load_session()
    timeline = {}
    contract = {}
    try:
        cfg = config_mod.load_config(CONFIG_PATH)
        timeline = cfg.get("timeline") or {}
        contract = cfg.get("contract") or {}
    except Exception:
        pass
    return jsonify({
        "files": s["files"], "checked": s["checked"],
        "has_issues": bool(s["issues"]),
        "has_fix_result": bool(s.get("fix_result")),
        "confirmed": is_confirmed(),
        "contract": contract, "timeline": timeline,
        "core_missing": [k for k, v in CORE_MODULES.items() if v is None],
    })


@app.post("/api/upload")
def api_upload():
    ensure_dirs()
    saved = []
    for f in request.files.getlist("files"):
        name = os.path.basename(f.filename or "")
        if not name or not name.lower().endswith(ALLOWED_EXT):
            continue
        path = os.path.join(UPLOAD_DIR, name)
        f.save(path)
        saved.append(add_file_entry(path))
    return jsonify({"ok": True, "saved": saved, "files": load_session()["files"]})


@app.post("/api/import_folder")
def api_import_folder():
    folder = (request.json or {}).get("path", "").strip()
    if not folder or not os.path.isdir(folder):
        return jsonify({"ok": False, "error": "文件夹不存在：" + folder}), 400
    added = []
    for name in sorted(os.listdir(folder)):
        if name.lower().endswith(ALLOWED_EXT):
            added.append(add_file_entry(os.path.join(folder, name)))
    if not added:
        return jsonify({"ok": False, "error": "该文件夹下没有 doc/docx/pdf 文件"}), 400
    return jsonify({"ok": True, "added": len(added), "files": load_session()["files"]})


@app.post("/api/files/delete")
def api_delete_file():
    path = (request.json or {}).get("path", "")
    s = load_session()
    s["files"] = [f for f in s["files"] if f["path"] != path]
    save_session(s)
    return jsonify({"ok": True, "files": s["files"]})


# ---------------- 页面1：合同抽取与确认 ----------------

@app.post("/api/extract_contract")
def api_extract_contract():
    s = load_session()
    contracts = [f for f in s["files"] if "合同" in f["name"]]
    if not contracts:
        return jsonify({"ok": False, "error": "文件列表中未找到文件名含“合同”的文件，请先上传。"}), 400
    path = contracts[0]["path"]
    tid = new_task("合同关键信息抽取中（扫描件 OCR 可能耗时数十秒）…")

    def work():
        fn = need(contract_profile_mod, "extract")
        _update_task(tid, label=f"正在抽取：{os.path.basename(path)}")
        profile = call_flex(fn, path)
        return {"profile": obj_to_dict(profile), "source": path}

    run_in_thread(tid, work)
    return jsonify({"ok": True, "task_id": tid})


@app.post("/api/confirm_contract")
def api_confirm_contract():
    data = request.json or {}
    p = data.get("profile") or {}
    tl = data.get("timeline") or {}
    profile = ContractProfile(
        project_name=p.get("project_name", "").strip(),
        employer=p.get("employer", "").strip(),
        contractors=[x.strip() for x in p.get("contractors", []) if str(x).strip()],
        supervisor=p.get("supervisor", "").strip(),
        amount_number=p.get("amount_number", "").strip(),
        amount_cn=p.get("amount_cn", "").strip(),
        payment_terms=[x.strip() for x in p.get("payment_terms", []) if str(x).strip()],
        service_period=p.get("service_period", "").strip(),
        contract_no=p.get("contract_no", "").strip(),
    )
    try:
        config_mod.confirm_contract(profile, CONFIG_PATH)
        cfg = config_mod.load_config(CONFIG_PATH)
        cfg["timeline"] = {
            "contract_signed": tl.get("contract_signed", ""),
            "work_order": tl.get("work_order", ""),
            "system_online": tl.get("system_online", ""),
            "acceptance_nodes": (cfg.get("timeline") or {}).get("acceptance_nodes", {}),
        }
        config_mod.save_config(cfg, CONFIG_PATH)
    except Exception as e:
        return jsonify({"ok": False, "error": f"写入配置失败：{e}"}), 500
    return jsonify({"ok": True})


# ---------------- 页面2：检查与判定 ----------------

@app.post("/api/check/start")
def api_check_start():
    if not is_confirmed():
        return jsonify({"ok": False, "error": "合同基准信息未确认，请先在页面1确认。"}), 403
    if engine is None:
        return jsonify({"ok": False, "error": "核心模块尚未就绪（engine）"}), 503
    s = load_session()
    if not s["files"]:
        return jsonify({"ok": False, "error": "尚未导入任何文档。"}), 400
    paths = [f["path"] for f in s["files"]]
    tid = new_task("准备检查…")
    if context_mod is not None:
        context_mod.clear_cache()  # 问题集变更，旧上下文截图失效

    def work():
        _update_task(tid, label="加载文档…")
        docs = engine.load_all(paths)
        _update_task(tid, label=f"已加载 {len(docs)} 份文档，开始检查")

        def cb(done, total, label):
            _update_task(tid, progress=done, total=total, label=label)

        issues = engine.run_checks(docs, CONFIG_PATH, progress_cb=cb)
        ss = load_session()
        ss["issues"] = [i.to_dict() for i in issues]
        ss["checked"] = True
        ss["fix_result"] = None
        save_session(ss)
        return {"count": len(issues)}

    run_in_thread(tid, work)
    return jsonify({"ok": True, "task_id": tid})


@app.get("/api/progress")
def api_progress():
    t = get_task(request.args.get("task_id", ""))
    if not t:
        return jsonify({"ok": False, "error": "任务不存在"}), 404
    return jsonify({"ok": True, "task": t})


@app.get("/api/task/<tid>")
def api_task(tid):
    t = get_task(tid)
    if not t:
        return jsonify({"ok": False, "error": "任务不存在"}), 404
    return jsonify({"ok": True, "task": t})


@app.get("/api/issues")
def api_issues():
    issues = load_session()["issues"]
    # 兼容评分功能上线前保存的历史会话。
    for it in issues:
        try:
            probability = float(it.get("error_probability"))
        except (TypeError, ValueError):
            probability = None
        if probability is None or not 0 <= probability <= 1:
            it["error_probability"] = 0.5
            it["probability_source"] = "heuristic"
            it["probability_reason"] = "历史问题未保存上下文评分，使用中性概率"
        it.setdefault("probability_source", "heuristic")
        it.setdefault("probability_reason", "历史问题未保存上下文评分，使用中性概率")
    return jsonify({"issues": issues})


_VALID_STATUS = {"是问题", "误报", "忽略", "待判定"}


@app.post("/api/issues/mark")
def api_mark():
    data = request.json or {}
    iid, status = data.get("id"), data.get("status")
    if status not in _VALID_STATUS:
        return jsonify({"ok": False, "error": "非法状态"}), 400
    s = load_session()
    n = 0
    for it in s["issues"]:
        if it["id"] == iid:
            it["status"] = status
            n += 1
    save_session(s)
    return jsonify({"ok": True, "updated": n})


@app.post("/api/issues/mark_all")
def api_mark_all():
    data = request.json or {}
    status = data.get("status")
    if status not in _VALID_STATUS - {"待判定"}:
        return jsonify({"ok": False, "error": "非法状态"}), 400
    category, rule_id = data.get("category"), data.get("rule_id")
    only_status = data.get("only_status")
    if only_status is not None and only_status not in _VALID_STATUS:
        return jsonify({"ok": False, "error": "非法原状态"}), 400
    try:
        min_probability = float(data["min_probability"]) if data.get("min_probability") not in (None, "") else None
        max_probability = float(data["max_probability"]) if data.get("max_probability") not in (None, "") else None
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "概率阈值必须是数字"}), 400
    if (min_probability is not None and not 0 <= min_probability <= 1) or \
       (max_probability is not None and not 0 <= max_probability <= 1):
        return jsonify({"ok": False, "error": "概率阈值应在 0 到 1 之间"}), 400
    if min_probability is not None and max_probability is not None and min_probability > max_probability:
        return jsonify({"ok": False, "error": "最低概率不能高于最高概率"}), 400
    s = load_session()
    n = 0
    for it in s["issues"]:
        if category and it.get("category") != category:
            continue
        if rule_id and it.get("rule_id") != rule_id:
            continue
        if only_status and it.get("status") != only_status:
            continue
        probability = float(it.get("error_probability", 0.5) or 0.5)
        if min_probability is not None and probability < min_probability:
            continue
        if max_probability is not None and probability > max_probability:
            continue
        it["status"] = status
        n += 1
    save_session(s)
    return jsonify({"ok": True, "updated": n})


# ---------------- 页面2：问题上下文 ----------------

@app.get("/api/issues/context")
def api_issue_context():
    if context_mod is None:
        return jsonify({"type": "error", "msg": "核心模块尚未就绪（context）"})
    iid = request.args.get("id", "")
    s = load_session()
    issue = next((it for it in s["issues"] if it["id"] == iid), None)
    if not issue:
        return jsonify({"type": "error", "msg": "问题不存在：" + iid}), 404
    try:
        return jsonify(context_mod.get_context(issue, s["files"]))
    except Exception as e:
        traceback.print_exc()
        return jsonify({"type": "error", "msg": f"上下文获取失败：{e}"})


@app.get("/api/context_img/<iid>")
def api_context_img(iid):
    if context_mod is None or not re.fullmatch(r"[\w\-]+", iid):
        return jsonify({"ok": False, "error": "非法请求"}), 400
    path = os.path.join(context_mod.CONTEXT_DIR, iid + ".png")
    base = os.path.abspath(context_mod.CONTEXT_DIR)
    if not os.path.abspath(path).startswith(base + os.sep) or not os.path.isfile(path):
        return jsonify({"ok": False, "error": "截图不存在"}), 404
    return send_file(path, mimetype="image/png")


# ---------------- 修复 ----------------

def _norm_fix_item(it):
    d = obj_to_dict(it)
    ok = d.get("ok", d.get("success"))
    status = d.get("status") or d.get("state") or (
        "成功" if ok else ("失败" if ok is False else ""))
    return {
        "issue_id": d.get("issue_id") or d.get("id", ""),
        "status": status,
        "reason": d.get("reason") or d.get("error") or d.get("msg", ""),
        "fix_detail": d.get("fix_detail") or d.get("detail", ""),
        "backup": d.get("backup") or d.get("backup_path", ""),
    }


@app.post("/api/fix")
def api_fix():
    if not is_confirmed():
        return jsonify({"ok": False, "error": "合同基准信息未确认。"}), 403
    s = load_session()
    if not s["issues"]:
        return jsonify({"ok": False, "error": "暂无检查结果，请先在页面2执行检查。"}), 400
    confirmed = [it for it in s["issues"] if it["status"] == "是问题"]
    if not confirmed:
        return jsonify({"ok": False, "error": "没有判定为“是问题”的条目。"}), 400
    fixable = [it for it in confirmed if it.get("fixable")]
    manual = [it for it in confirmed if not it.get("fixable")]
    path_by_name = {f["name"]: f["path"] for f in s["files"]}
    path_by_stem = {os.path.splitext(f["name"])[0]: f["path"] for f in s["files"]}
    tid = new_task("修复中…")

    def work():
        fn = need(repair_mod, "apply_fixes")
        by_doc = {}
        for it in fixable:
            by_doc.setdefault(it["doc_name"], []).append(issue_from_dict(it))
        items, backups, outputs = [], [], []
        total = max(len(by_doc), 1)
        for i, (doc_name, doc_issues) in enumerate(by_doc.items()):
            _update_task(tid, progress=i, total=total, label=f"修复 {doc_name}")
            doc_path = path_by_name.get(doc_name) or path_by_stem.get(
                os.path.splitext(doc_name)[0], "")
            try:
                res = call_flex(fn, doc_path, doc_issues, CONFIG_PATH)
                rd = obj_to_dict(res)
                per_issue = (rd.get("items") or rd.get("details")
                             or rd.get("results") or rd.get("per_issue"))
                if per_issue is None and any(
                        k in rd for k in ("succeeded", "failed", "skipped")):
                    per_issue = []
                    for d in rd.get("succeeded") or []:
                        per_issue.append({**d, "ok": True,
                                          "fix_detail": f"{d.get('before', '')} → {d.get('after', '')}"})
                    for d in rd.get("failed") or []:
                        per_issue.append({**d, "ok": False})
                    for d in rd.get("skipped") or []:
                        per_issue.append({**d, "status": "跳过"})
                for it in per_issue or []:
                    items.append(_norm_fix_item(it))
                bp = rd.get("backup_path") or rd.get("backup")
                backups += [p for p in (rd.get("backups") or rd.get("backup_paths")
                                        or ([bp] if bp else [])) if p]
                outputs += [p for p in (rd.get("outputs") or rd.get("fixed_files")
                                        or ([rd["output"]] if rd.get("output") else [])
                                        or ([rd["fixed_path"]] if rd.get("fixed_path") else [])
                                        or ([doc_path] if rd.get("succeeded") else [])) if p]
                if not items and rd:
                    items.append({"issue_id": "", "status": "成功",
                                  "reason": "", "fix_detail": str(rd), "backup": ""})
            except Exception as e:
                for di in doc_issues:
                    items.append({"issue_id": di.id, "status": "失败",
                                  "reason": str(e), "fix_detail": "", "backup": ""})
        # 更新问题状态
        ss = load_session()
        st_by_id = {it["issue_id"]: it for it in items if it["issue_id"]}
        for it in ss["issues"]:
            r = st_by_id.get(it["id"])
            if r:
                it["status"] = "已修复" if r["status"] == "成功" else (
                    "修复失败" if r["status"] == "失败" else it["status"])
                it["fix_detail"] = r.get("fix_detail", "")
            elif it["status"] == "是问题" and not it.get("fixable"):
                it["status"] = "跳过修复"
        ss["fix_result"] = {
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "items": items,
            "manual": manual,
            "backups": backups,
            "outputs": outputs,
        }
        save_session(ss)
        _update_task(tid, progress=total, total=total, label="完成")
        return {"fixed": sum(1 for x in items if x["status"] == "成功"),
                "failed": sum(1 for x in items if x["status"] == "失败"),
                "manual": len(manual)}

    run_in_thread(tid, work)
    return jsonify({"ok": True, "task_id": tid})


@app.get("/api/fix_result")
def api_fix_result():
    return jsonify({"fix_result": load_session().get("fix_result")})


@app.post("/api/rollback")
def api_rollback():
    s = load_session()
    fr = s.get("fix_result")
    if not fr:
        return jsonify({"ok": False, "error": "没有可回滚的修复记录。"}), 400
    try:
        fn = need(repair_mod, "rollback")
        results = []
        for bp in fr.get("backups") or []:
            r = fn(bp)  # 核心签名: rollback(backup_path_or_id) -> (ok, message)
            results.append({"backup": bp, "result": r if isinstance(r, str) else list(r) if isinstance(r, tuple) else obj_to_dict(r)})
        if not results:
            r = call_flex(fn, fr)
            results.append({"backup": "", "result": obj_to_dict(r) if r is not None else {}})
        ok = all(isinstance(x["result"], list) and x["result"][0] for x in results if isinstance(x["result"], list)) or not results
        return jsonify({"ok": ok, "results": results})
    except CoreNotReady as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    except Exception as e:
        return jsonify({"ok": False, "error": f"回滚失败：{e}"}), 500


@app.get("/api/download")
def api_download():
    path = request.args.get("path", "")
    if not _allowed_download(path):
        return jsonify({"ok": False, "error": "文件不可下载"}), 403
    return send_file(path, as_attachment=True,
                     download_name=os.path.basename(path))


# ---------------- 页面4：工具箱 ----------------

@app.post("/api/tools/upload")
def api_tools_upload():
    ensure_dirs()
    saved = []
    for f in request.files.getlist("files"):
        name = os.path.basename(f.filename or "")
        if not name:
            continue
        path = os.path.join(TOOLS_DIR, name)
        f.save(path)
        saved.append({"name": name, "path": path, "size_h": fmt_size(os.path.getsize(path))})
    return jsonify({"ok": True, "files": saved})


@app.post("/api/tools/stamp/preview")
def api_stamp_preview():
    data = request.json or {}
    try:
        fn = need(pdfstamp_mod, "preview")
        png = call_flex(fn, data.get("pdf_path", ""), data.get("lines") or [],
                        position=data.get("position", "bottom_center"),
                        font_size=int(data.get("font_size") or 14),
                        x=data.get("x"), y=data.get("y"))
        if isinstance(png, str):
            with open(png, "rb") as f:
                png = f.read()
        return Response(bytes(png), mimetype="image/png")
    except CoreNotReady as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    except Exception as e:
        return jsonify({"ok": False, "error": f"预览失败：{e}"}), 500


@app.post("/api/tools/stamp/apply")
def api_stamp_apply():
    data = request.json or {}
    pdfs = data.get("pdf_paths") or []
    lines = data.get("lines") or []
    if not pdfs or not lines:
        return jsonify({"ok": False, "error": "请选择 PDF 并填写署名内容。"}), 400
    tid = new_task("批量署名中…")

    def work():
        stamp = need(pdfstamp_mod, "stamp_first_page")
        results = []
        for i, pdf in enumerate(pdfs):
            _update_task(tid, progress=i, total=len(pdfs),
                         label=f"处理 {os.path.basename(pdf)}")
            out = os.path.splitext(pdf)[0] + "_署名.pdf"
            try:
                try:
                    already = call_flex(need(pdfstamp_mod, "already_stamped"), pdf, lines)
                except CoreNotReady:
                    already = False
                if already:
                    results.append({"pdf": pdf, "ok": True, "skipped": True,
                                    "msg": "已署名，跳过", "out": ""})
                    continue
                r = call_flex(stamp, pdf, out, lines,
                              position=data.get("position", "bottom_center"),
                              font_size=int(data.get("font_size") or 14),
                              x=data.get("x"), y=data.get("y"))
                rd = _norm_convert_result(r)
                rd.setdefault("out", out)
                if not rd.get("out"):
                    rd["out"] = out
                results.append({"pdf": pdf, "skipped": False, **rd})
            except Exception as e:
                results.append({"pdf": pdf, "ok": False, "skipped": False,
                                "msg": str(e), "out": ""})
        return {"results": results}

    run_in_thread(tid, work)
    return jsonify({"ok": True, "task_id": tid})


@app.post("/api/tools/convert")
def api_convert():
    data = request.json or {}
    path, mode = data.get("path", ""), data.get("mode", "")
    if not os.path.isfile(path):
        return jsonify({"ok": False, "error": "文件不存在"}), 400
    tid = new_task("转换中…")

    def work():
        if mode == "pdf2word":
            ocr = data.get("force_ocr") or not pdf_has_text(path)
            fname = "pdf_ocr_to_docx" if ocr else "pdf_to_docx"
            if ocr:
                _update_task(tid, label="未检测到文本层，走 OCR 转换（可能耗时较长）…")
            out = os.path.splitext(path)[0] + ".docx"
        elif mode == "word2pdf":
            fname, out = "word_to_pdf", os.path.splitext(path)[0] + ".pdf"
        elif mode == "doc2docx":
            fname, out = "doc_to_docx", os.path.splitext(path)[0] + ".docx"
        else:
            raise ValueError("未知转换类型")
        fn = need(convert_mod, fname)
        if fname == "doc_to_docx":
            r = call_flex(fn, path)
        else:
            r = call_flex(fn, path, out)
        rd = _norm_convert_result(r)
        if not rd.get("out"):
            rd["out"] = out
        rd["ocr"] = (mode == "pdf2word" and (data.get("force_ocr") or not pdf_has_text(path)))
        return rd

    run_in_thread(tid, work)
    return jsonify({"ok": True, "task_id": tid})


@app.post("/api/tools/guide")
def api_guide():
    kind = (request.json or {}).get("kind", "doc")
    s = load_session()
    if not s["files"]:
        return jsonify({"ok": False, "error": "尚未导入任何文档。"}), 400
    if engine is None:
        return jsonify({"ok": False, "error": "核心模块尚未就绪（engine）"}), 503
    paths = [f["path"] for f in s["files"]]
    tid = new_task("生成指引册…")

    def work():
        ensure_dirs()
        docs = engine.load_all(paths)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        if kind == "brief":
            fn = need(guide_mod, "generate_brief_guide")
            out = os.path.join(REPORT_DIR, f"汇报材料指引册_{ts}.docx")
            cfg = config_mod.load_config(CONFIG_PATH)
            c = cfg.get("contract") or {}
            contract = ContractProfile(**{k: c.get(k) for k in (
                "project_name", "employer", "contractors", "supervisor",
                "amount_number", "amount_cn", "payment_terms",
                "service_period", "contract_no")})
            call_flex(fn, docs, contract, out)
        else:
            fn = need(guide_mod, "generate_doc_guide")
            out = os.path.join(REPORT_DIR, f"文档指引册_{ts}.docx")
            call_flex(fn, docs, out)
        return {"out": out, "name": os.path.basename(out)}

    run_in_thread(tid, work)
    return jsonify({"ok": True, "task_id": tid})


@app.post("/api/tools/export")
def api_export():
    fmt = (request.json or {}).get("fmt", "excel")
    s = load_session()
    if not s["issues"]:
        return jsonify({"ok": False, "error": "暂无检查结果，请先在页面2执行检查。"}), 400
    try:
        ensure_dirs()
        issues = [issue_from_dict(d) for d in s["issues"]]
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        if fmt == "json":
            fn = need(report_mod, "export_json")
            out = os.path.join(REPORT_DIR, f"检查报告_{ts}.json")
        else:
            fn = need(report_mod, "export_excel")
            out = os.path.join(REPORT_DIR, f"检查报告_{ts}.xlsx")
        call_flex(fn, issues, out)
        return jsonify({"ok": True, "out": out, "name": os.path.basename(out)})
    except CoreNotReady as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    except Exception as e:
        return jsonify({"ok": False, "error": f"导出失败：{e}"}), 500


@app.errorhandler(Exception)
def on_error(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    traceback.print_exc()
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": f"服务器内部错误：{e}"}), 500
    return f"<h3>服务器内部错误</h3><pre>{e}</pre>", 500


def main():
    ensure_dirs()
    app.run(host="127.0.0.1", port=8600, debug=False, threaded=True)


if __name__ == "__main__":
    main()
