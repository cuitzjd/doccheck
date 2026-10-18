"""本地 OCR 提供者链（文稿涉密，不调用任何云服务）。

后端优先级：
1. tesseract CLI（需自行安装并具备 chi_sim 语言包）；
2. macOS Vision 框架（VNRecognizeTextRequest，zh-Hans）——通过一段临时 Swift
   脚本实现，首次使用时 swiftc 编译并缓存到 ~/.doccheck/bin/，系统自带无需安装；
3. 都不可用时 ocr_pdf 返回空串（调用方应提示用户改用手工录入基准字段）。

本机实测：无 tesseract；Vision 路径可用（Swift 编译约 20s，之后每页约 5~15s）。
"""
import hashlib
import os
import shutil
import subprocess
import tempfile

BIN_DIR = os.path.expanduser("~/.doccheck/bin")

_VISION_SRC = r"""
import Foundation
import Vision

let args = CommandLine.arguments
guard args.count > 1 else {
    FileHandle.standardError.write("usage: ocr <image>\n".data(using: .utf8)!)
    exit(2)
}
let url = URL(fileURLWithPath: args[1])
let request = VNRecognizeTextRequest()
request.recognitionLanguages = ["zh-Hans"]
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
let handler = VNImageRequestHandler(url: url, options: [:])
try handler.perform([request])
for obs in request.results ?? [] {
    if let c = obs.topCandidates(1).first {
        print(c.string)
    }
}
"""


def _has_tesseract() -> bool:
    exe = shutil.which("tesseract")
    if not exe:
        return False
    try:
        out = subprocess.run([exe, "--list-langs"], capture_output=True,
                             text=True, timeout=20)
        return "chi_sim" in out.stdout
    except Exception:
        return False


def _vision_bin() -> str:
    """编译（带缓存）Vision OCR 助手，返回可执行文件路径；不可用时返回空串。"""
    if not shutil.which("swiftc"):
        return ""
    os.makedirs(BIN_DIR, exist_ok=True)
    tag = hashlib.md5(_VISION_SRC.encode()).hexdigest()[:10]
    binpath = os.path.join(BIN_DIR, f"doccheck-ocr-vision-{tag}")
    if os.path.exists(binpath) and os.access(binpath, os.X_OK):
        return binpath
    src = os.path.join(BIN_DIR, f"doccheck-ocr-vision-{tag}.swift")
    with open(src, "w", encoding="utf-8") as f:
        f.write(_VISION_SRC)
    try:
        subprocess.run(["swiftc", "-O", "-o", binpath, src],
                       check=True, capture_output=True, timeout=300)
        return binpath
    except Exception:
        return ""


def available() -> str:
    """返回当前可用 OCR 后端名：'tesseract' / 'vision' / ''（不可用）。"""
    if _has_tesseract():
        return "tesseract"
    if _vision_bin():
        return "vision"
    return ""


def _render_pages(pdf_path: str, pages, dpi: int, outdir: str) -> list:
    import fitz
    pdf = fitz.open(pdf_path)
    if pages is None:
        pages = range(pdf.page_count)
    imgs = []
    for pno in pages:
        if pno >= pdf.page_count:
            break
        pix = pdf[pno].get_pixmap(dpi=dpi)
        fp = os.path.join(outdir, f"page_{pno + 1}.png")
        pix.save(fp)
        imgs.append((pno, fp))
    pdf.close()
    return imgs


def _ocr_image(backend: str, img: str, vision_bin: str = "") -> str:
    if backend == "tesseract":
        out = subprocess.run(["tesseract", img, "stdout", "-l", "chi_sim"],
                             capture_output=True, text=True, timeout=300)
        return out.stdout
    out = subprocess.run([vision_bin, img], capture_output=True, text=True,
                         timeout=300)
    return out.stdout


def ocr_pdf(pdf_path: str, pages=None, dpi: int = 200) -> str:
    """对 PDF 指定页（0 起，None=全部）做本地 OCR，返回拼接文本（页间以换页注释分隔）。

    无可用后端时返回空串（调用方据 available()=='' 提示手工录入回退）。
    """
    backend = available()
    if not backend:
        return ""
    vision_bin = _vision_bin() if backend == "vision" else ""
    parts = []
    with tempfile.TemporaryDirectory(prefix="doccheck_ocr_") as td:
        for pno, img in _render_pages(pdf_path, pages, dpi, td):
            text = _ocr_image(backend, img, vision_bin).strip()
            parts.append(f"===== 第{pno + 1}页 =====\n{text}")
    return "\n".join(parts)
