import os
import posixpath
import re
from html import escape, unescape

SUPPORTED_IMAGE_TYPES = {
    "jpg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}


def run(bk):
    """
    Sigil 插件主入口函数
    :param bk: BookContainer 对象，提供与当前 EPUB 交互的 API
    """
    # 1. 尝试从元数据中获取当前标题作为默认值
    default_title = ""
    # 注意：不同版本 Sigil 的 getmetadata 行为可能略有不同，这里作为容错处理
    try:
        title_meta = bk.getmetadata('dc:title')
        if title_meta:
            # title_meta 通常是字典或列表结构
            default_title = title_meta[0].get('content', '') if isinstance(title_meta, list) else title_meta
    except Exception:
        pass

    target_title = ask_string("获取封面", "请输入作品名称：", default_title)
    if not target_title:
        return 0

    # 2. 网络搜索与图片获取
    img_data = fetch_cover_from_web(target_title)
    
    if not img_data:
        show_message("warning", "结果", "未能找到封面或下载失败。")
        return -1

    image_info = detect_image_type(img_data)
    if not image_info:
        show_message("warning", "结果", "已下载封面，但图片格式不是 EPUB 常用格式（JPEG/JFIF、PNG、GIF、WebP）。")
        return -1

    ext, mime = image_info

    # 3. 解析 OPF 文件并替换或新增封面数据
    try:
        epub3 = is_epub3(bk)
        cover_file_id = find_current_cover_id(bk, prefer_epub3=epub3)

        if cover_file_id and can_overwrite_cover(bk, cover_file_id, mime):
            bk.writefile(cover_file_id, img_data)
            ensure_cover_semantics(bk, cover_file_id, epub3)
            ensure_cover_page(bk, cover_file_id, epub3)
            show_message("info", "成功", "封面下载完成，已替换当前封面并更新封面页。")
        else:
            new_cover_id = add_new_cover_file(bk, img_data, ext, mime, epub3)
            ensure_cover_semantics(bk, new_cover_id, epub3, old_cover_id=cover_file_id)
            ensure_cover_page(bk, new_cover_id, epub3)
            show_message("info", "成功", "封面下载完成，已新增并设置为当前 EPUB 封面页。")
            
    except Exception as e:
        show_message("error", "写入错误", f"替换封面时发生异常：{str(e)}")
        return -1

    return 0


def load_qt():
    qt_version = os.environ.get("SIGIL_QT_RUNTIME_VERSION", "6.5.2")
    qt_major = int(qt_version.split(".", 1)[0])

    if qt_major == 6:
        try:
            from PySide6 import QtCore, QtWidgets
            return QtCore, QtWidgets
        except ImportError:
            pass
    if qt_major == 5:
        try:
            from PyQt5 import QtCore, QtWidgets
            return QtCore, QtWidgets
        except ImportError:
            pass

    try:
        from PySide6 import QtCore, QtWidgets
        return QtCore, QtWidgets
    except ImportError:
        from PyQt5 import QtCore, QtWidgets
        return QtCore, QtWidgets


def ensure_qt_app(QtWidgets):
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def exec_dialog(dialog):
    if hasattr(dialog, "exec"):
        return dialog.exec()
    return dialog.exec_()


def raise_to_front(dialog, QtCore):
    try:
        dialog.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, True)
    except AttributeError:
        dialog.setWindowFlags(dialog.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()


def ask_string(title, label, initialvalue=""):
    QtCore, QtWidgets = load_qt()
    ensure_qt_app(QtWidgets)

    dialog = QtWidgets.QInputDialog()
    dialog.setWindowTitle(title)
    dialog.setLabelText(label)
    dialog.setTextValue(initialvalue or "")
    raise_to_front(dialog, QtCore)
    accepted = exec_dialog(dialog)
    if not accepted:
        return None
    return dialog.textValue().strip()


def show_message(kind, title, text):
    QtCore, QtWidgets = load_qt()
    ensure_qt_app(QtWidgets)

    icon_map = {
        "info": QtWidgets.QMessageBox.Information,
        "warning": QtWidgets.QMessageBox.Warning,
        "error": QtWidgets.QMessageBox.Critical,
    }
    box = QtWidgets.QMessageBox()
    box.setIcon(icon_map.get(kind, QtWidgets.QMessageBox.Information))
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QtWidgets.QMessageBox.Ok)
    raise_to_front(box, QtCore)
    exec_dialog(box)


def detect_image_type(img_data):
    """
    根据文件头识别 EPUB 常用图片格式。
    JFIF 是 JPEG 的常见封装，统一按 .jpg / image/jpeg 写入。
    """
    if img_data.startswith(b"\xff\xd8\xff"):
        return "jpg", SUPPORTED_IMAGE_TYPES["jpg"]
    if img_data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", SUPPORTED_IMAGE_TYPES["png"]
    if img_data.startswith((b"GIF87a", b"GIF89a")):
        return "gif", SUPPORTED_IMAGE_TYPES["gif"]
    if img_data.startswith(b"\x52\x49\x46\x46") and img_data[8:12] == b"WEBP":
        return "webp", SUPPORTED_IMAGE_TYPES["webp"]
    return None


def is_epub3(bk):
    try:
        return str(bk.epub_version()).startswith("3")
    except Exception:
        opf_data = safe_get_opf(bk)
        match = re.search(r'<package\b[^>]*\bversion=["\']([^"\']+)["\']', opf_data, re.I)
        return bool(match and match.group(1).startswith("3"))


def safe_get_opf(bk):
    try:
        return bk.get_opf()
    except Exception:
        return ""


def parse_attrs(tag_text):
    attrs = {}
    for match in re.finditer(r'([A-Za-z_:][A-Za-z0-9_.:-]*)\s*=\s*(["\'])(.*?)\2', tag_text):
        attrs[match.group(1)] = match.group(3)
    return attrs


def find_current_cover_id(bk, prefer_epub3=False):
    opf_data = safe_get_opf(bk)
    epub2_id = find_epub2_cover_id(opf_data)
    epub3_id = find_epub3_cover_id(opf_data)
    if prefer_epub3:
        return epub3_id or epub2_id
    return epub2_id or epub3_id


def find_epub2_cover_id(opf_data):
    for match in re.finditer(r'<meta\b[^>]*>', opf_data, re.I):
        attrs = parse_attrs(match.group(0))
        if attrs.get("name") == "cover" and attrs.get("content"):
            return attrs["content"]
    return None


def find_epub3_cover_id(opf_data):
    for match in re.finditer(r'<item\b[^>]*>', opf_data, re.I):
        attrs = parse_attrs(match.group(0))
        properties = attrs.get("properties", "")
        if "cover-image" in properties.split() and attrs.get("id"):
            return attrs["id"]
    return None


def can_overwrite_cover(bk, cover_id, new_mime):
    old_mime = get_id_mime(bk, cover_id)
    if old_mime == new_mime:
        return True

    old_href = get_id_href(bk, cover_id)
    old_ext_mime = mime_from_href(old_href)
    if old_ext_mime == new_mime:
        return True

    # 避免把 JPEG 数据写进 PNG/GIF 文件，造成 manifest 和真实内容不一致。
    return False


def get_id_mime(bk, manifest_id):
    try:
        return bk.id_to_mime(manifest_id, None)
    except Exception:
        for item_id, _href, mime in safe_manifest_iter(bk):
            if item_id == manifest_id:
                return mime
    return None


def get_id_href(bk, manifest_id):
    try:
        return bk.id_to_href(manifest_id, None)
    except Exception:
        for item_id, href, _mime in safe_manifest_iter(bk):
            if item_id == manifest_id:
                return href
    return None


def safe_manifest_iter(bk):
    try:
        return list(bk.manifest_iter())
    except Exception:
        return []


def mime_from_href(href):
    if not href:
        return None
    ext = href.rsplit(".", 1)[-1].lower() if "." in href else ""
    if ext in ("jpg", "jpeg", "jfif"):
        return SUPPORTED_IMAGE_TYPES["jpg"]
    return SUPPORTED_IMAGE_TYPES.get(ext)


def add_new_cover_file(bk, img_data, ext, mime, epub3):
    unique_id, filename, bookpath = choose_unique_cover_target(bk, ext)

    if bookpath and hasattr(bk, "addbookpath"):
        bk.addbookpath(unique_id, bookpath, img_data, mime)
    else:
        try:
            bk.addfile(unique_id, filename, img_data, mime, "cover-image" if epub3 else None)
        except TypeError:
            bk.addfile(unique_id, filename, img_data, mime)

    return unique_id


def choose_unique_cover_target(bk, ext):
    folder = get_default_image_folder(bk)
    for index in range(0, 1000):
        suffix = "" if index == 0 else f"_{index + 1}"
        unique_id = f"autocover{suffix}"
        filename = f"autocover{suffix}.{ext}"
        bookpath = f"{folder}/{filename}" if folder else None
        if not manifest_id_exists(bk, unique_id) and not basename_exists(bk, filename) and not bookpath_exists(bk, bookpath):
            return unique_id, filename, bookpath
    raise RuntimeError("无法生成唯一的封面文件名。")


def get_default_image_folder(bk):
    try:
        folders = bk.group_to_folders("Images", None)
        if folders:
            return folders[0].strip("/")
    except Exception:
        pass
    return "Images"


def manifest_id_exists(bk, manifest_id):
    return get_id_href(bk, manifest_id) is not None


def basename_exists(bk, filename):
    try:
        return bk.basename_to_id(filename, None) is not None
    except Exception:
        return False


def bookpath_exists(bk, bookpath):
    if not bookpath:
        return False
    try:
        return bk.bookpath_to_id(bookpath, None) is not None
    except Exception:
        return False


def ensure_cover_semantics(bk, cover_id, epub3, old_cover_id=None):
    if epub3:
        set_epub3_cover_image(bk, cover_id, old_cover_id)
    else:
        set_epub2_cover_meta(bk, cover_id)


def set_epub3_cover_image(bk, cover_id, old_cover_id=None):
    if not hasattr(bk, "set_manifest_epub3_attributes"):
        return

    if old_cover_id and old_cover_id != cover_id:
        old_props = remove_property(get_epub3_properties(bk, old_cover_id), "cover-image")
        bk.set_manifest_epub3_attributes(
            old_cover_id,
            properties=old_props,
            fallback=get_epub3_fallback(bk, old_cover_id),
            overlay=get_epub3_overlay(bk, old_cover_id),
        )

    new_props = add_property(get_epub3_properties(bk, cover_id), "cover-image")
    bk.set_manifest_epub3_attributes(
        cover_id,
        properties=new_props,
        fallback=get_epub3_fallback(bk, cover_id),
        overlay=get_epub3_overlay(bk, cover_id),
    )


def get_epub3_properties(bk, manifest_id):
    try:
        return bk.id_to_properties(manifest_id, None)
    except Exception:
        return None


def get_epub3_fallback(bk, manifest_id):
    try:
        return bk.id_to_fallback(manifest_id, None)
    except Exception:
        return None


def get_epub3_overlay(bk, manifest_id):
    try:
        return bk.id_to_overlay(manifest_id, None)
    except Exception:
        return None


def add_property(properties, prop):
    parts = [item for item in (properties or "").split() if item]
    if prop not in parts:
        parts.append(prop)
    return " ".join(parts) if parts else None


def remove_property(properties, prop):
    parts = [item for item in (properties or "").split() if item and item != prop]
    return " ".join(parts) if parts else None


def set_epub2_cover_meta(bk, cover_id):
    if not hasattr(bk, "getmetadataxml") or not hasattr(bk, "setmetadataxml"):
        return

    metadata = bk.getmetadataxml()
    metadata = re.sub(
        r'\s*<meta\b(?=[^>]*\bname\s*=\s*(["\'])cover\1)[^>]*/?>',
        "",
        metadata,
        flags=re.I,
    )
    cover_meta = f'  <meta name="cover" content="{cover_id}"/>\n'
    if re.search(r'</metadata\s*>', metadata, re.I):
        metadata = re.sub(r'</metadata\s*>', cover_meta + r'</metadata>', metadata, count=1, flags=re.I)
    else:
        metadata = metadata + "\n" + cover_meta
    bk.setmetadataxml(metadata)


def ensure_cover_page(bk, cover_image_id, epub3):
    cover_page_id, cover_page_bookpath, created = choose_cover_page_target(bk)
    image_bookpath = get_manifest_bookpath(bk, cover_image_id)
    if not image_bookpath:
        raise RuntimeError("无法定位封面图片路径。")

    image_href = make_relative_href(cover_page_bookpath, image_bookpath)
    cover_xhtml = build_cover_xhtml(image_href)

    if created:
        if cover_page_bookpath and hasattr(bk, "addbookpath"):
            bk.addbookpath(cover_page_id, cover_page_bookpath, cover_xhtml, "application/xhtml+xml")
        else:
            filename = posixpath.basename(cover_page_bookpath or "cover.xhtml")
            bk.addfile(cover_page_id, filename, cover_xhtml, "application/xhtml+xml")
    else:
        bk.writefile(cover_page_id, cover_xhtml)

    move_spine_item_to_front(bk, cover_page_id, epub3)
    ensure_guide_cover(bk, cover_page_id)


def choose_cover_page_target(bk):
    existing_id = find_existing_cover_page_id(bk)
    if existing_id:
        return existing_id, get_manifest_bookpath(bk, existing_id) or "cover.xhtml", False

    folder = get_default_text_folder(bk)
    for index in range(0, 1000):
        suffix = "" if index == 0 else f"_{index + 1}"
        unique_id = f"autocover_page{suffix}"
        filename = "cover.xhtml" if index == 0 else f"cover{suffix}.xhtml"
        bookpath = f"{folder}/{filename}" if folder else filename
        if not manifest_id_exists(bk, unique_id) and not basename_exists(bk, filename) and not bookpath_exists(bk, bookpath):
            return unique_id, bookpath, True
    raise RuntimeError("无法生成唯一的封面页文件名。")


def find_existing_cover_page_id(bk):
    for filename in ("cover.xhtml", "cover.html"):
        try:
            manifest_id = bk.basename_to_id(filename, None)
        except Exception:
            manifest_id = None
        if manifest_id and get_id_mime(bk, manifest_id) == "application/xhtml+xml":
            return manifest_id

    try:
        for guide_type, _title, href in bk.getguide():
            if guide_type == "cover":
                manifest_id = bk.href_to_id(href, None)
                if manifest_id and get_id_mime(bk, manifest_id) == "application/xhtml+xml":
                    return manifest_id
    except Exception:
        pass

    return None


def get_default_text_folder(bk):
    try:
        folders = bk.group_to_folders("Text", None)
        if folders:
            return folders[0].strip("/")
    except Exception:
        pass
    return "Text"


def get_manifest_bookpath(bk, manifest_id):
    try:
        bookpath = bk.id_to_bookpath(manifest_id, None)
        if bookpath:
            return bookpath
    except Exception:
        pass
    return get_id_href(bk, manifest_id)


def make_relative_href(from_bookpath, to_bookpath):
    from_dir = posixpath.dirname(from_bookpath or "")
    if not from_dir:
        return to_bookpath
    return posixpath.relpath(to_bookpath, from_dir)


def build_cover_xhtml(image_href):
    safe_href = escape(image_href, quote=True)
    return f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>Cover</title>
  <meta http-equiv="Content-Type" content="text/html; charset=utf-8" />
  <style type="text/css">
    html, body {{
      margin: 0;
      padding: 0;
      height: 100%;
    }}
    body {{
      text-align: center;
    }}
    .cover {{
      height: 100vh;
      width: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    img {{
      max-height: 100%;
      max-width: 100%;
    }}
  </style>
</head>
<body>
  <div class="cover">
    <img alt="Cover" src="{safe_href}" />
  </div>
</body>
</html>
'''


def move_spine_item_to_front(bk, manifest_id, epub3):
    if epub3 and hasattr(bk, "getspine_epub3") and hasattr(bk, "setspine_epub3"):
        spine = [item for item in bk.getspine_epub3() if item[0] != manifest_id]
        spine.insert(0, (manifest_id, "yes", None))
        bk.setspine_epub3(spine)
        return

    if hasattr(bk, "getspine") and hasattr(bk, "setspine"):
        spine = [item for item in bk.getspine() if item[0] != manifest_id]
        spine.insert(0, (manifest_id, "yes"))
        bk.setspine(spine)
        return

    if hasattr(bk, "spine_insert_before"):
        bk.spine_insert_before(0, manifest_id, "yes", None if epub3 else None)


def ensure_guide_cover(bk, cover_page_id):
    if not hasattr(bk, "getguide") or not hasattr(bk, "setguide"):
        return

    href = get_id_href(bk, cover_page_id)
    if not href:
        return

    guide = []
    try:
        for guide_type, title, target_href in bk.getguide():
            if guide_type != "cover":
                guide.append((guide_type, title, target_href))
    except Exception:
        guide = []

    guide.insert(0, ("cover", "Cover", href))
    bk.setguide(guide)


def fetch_cover_from_web(title):
    """
    针对起点中文网的网络通信与解析模块。
    先通过移动端搜索页解析书号，再用阅文封面 CDN 直接下载封面。
    """
    import urllib.request
    import urllib.parse

    # 构造请求头，模拟现代浏览器以降低被拦截概率
    headers = {
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Referer': 'https://m.qidian.com/'
    }

    try:
        book_id = extract_qidian_book_id(title)
        if not book_id:
            book_id = search_qidian_book_id(title, headers, urllib.request, urllib.parse)

        if not book_id:
            return None

        cover_url = build_qidian_cover_url(book_id)

        # 请求真实图片数据。
        # 必须重写 Referer 伪装图片请求来源，规避 CDN 防盗链（Hotlinking protection）
        img_headers = headers.copy()
        img_headers['Referer'] = f'https://m.qidian.com/chapter/{book_id}/0/'
        
        req_img = urllib.request.Request(cover_url, headers=img_headers)
        with urllib.request.urlopen(req_img, timeout=15) as img_res:
            return img_res.read()

    except Exception:
        # 捕获网络超时、403 禁止访问或解析异常
        # 生产环境中可将异常写入 Sigil 控制台：print(e)
        return None


def extract_qidian_book_id(text):
    text = (text or "").strip()
    if re.fullmatch(r"\d{6,}", text):
        return text

    if "qidian.com" not in text:
        return None

    match = re.search(r'(?:book|info|chapter|honor|fansrank|all-catalog)/(\d{6,})', text)
    if match:
        return match.group(1)
    return None


def search_qidian_book_id(title, headers, request_module, parse_module):
    query = (title or "").strip()
    if not query:
        return None

    search_url = f"https://m.qidian.com/search?kw={parse_module.quote(query)}"
    req = request_module.Request(search_url, headers=headers)
    with request_module.urlopen(req, timeout=15) as response:
        html_content = response.read().decode("utf-8", "replace")

    return parse_qidian_mobile_search(html_content, query)


def parse_qidian_mobile_search(html_content, query):
    candidates = []
    for match in re.finditer(r'<a\b(?=[^>]*\bdata-bid=(["\'])(\d{6,})\1)[^>]*>.*?</a>', html_content, re.I | re.S):
        item_html = match.group(0)
        attrs = parse_attrs(item_html.split(">", 1)[0] + ">")
        book_id = attrs.get("data-bid")
        title = extract_search_item_title(item_html, attrs)
        if book_id:
            candidates.append((book_id, title))

    if not candidates:
        return None

    query_norm = normalize_title(query)
    for book_id, candidate_title in candidates:
        if normalize_title(candidate_title) == query_norm:
            return book_id

    for book_id, candidate_title in candidates:
        candidate_norm = normalize_title(candidate_title)
        if query_norm and (query_norm in candidate_norm or candidate_norm in query_norm):
            return book_id

    return candidates[0][0]


def extract_search_item_title(item_html, attrs):
    attr_title = attrs.get("title", "")
    if attr_title:
        return re.sub(r"在线阅读\s*$", "", attr_title).strip()

    match = re.search(r'<h2\b[^>]*>(.*?)</h2>', item_html, re.I | re.S)
    if match:
        return clean_html_text(match.group(1))

    return ""


def clean_html_text(raw_html):
    text = re.sub(r"<[^>]+>", "", raw_html)
    return unescape(text).strip()


def normalize_title(text):
    text = unescape(text or "").lower()
    return re.sub(r"[\s《》<>\"'“”‘’\[\]【】（）()，,。.!！?？:：;；、_-]+", "", text)


def build_qidian_cover_url(book_id):
    return f"https://bookcover.yuewen.com/qdbimg/349573/{book_id}/600"
