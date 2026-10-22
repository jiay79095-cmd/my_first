import os
import socket
import argparse
from io import BytesIO
from flask import Flask, render_template_string, request, send_file, jsonify, Response
from werkzeug.utils import secure_filename

app = Flask(__name__)
# 工作根目录：当前脚本所在目录
BASE_DIR = os.path.abspath(".")

# 内嵌前端HTML页面，无外部templates文件
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>局域网文件传输工具</title>
    <style>
        * { box-sizing: border-box; margin: 10px; font-family: "Microsoft Yahei"; }
        .container { max-width: 900px; margin: 20px auto; }
        .path-bar { padding: 8px; background: #eee; border-radius: 4px; word-break: break-all; }
        .path-seg { color: #0066cc; cursor: pointer; text-decoration: underline; }
        .path-seg:hover { color: #004499; }
        .path-split { color: #666; margin: 0 6px; }
        .file-item { display: flex; align-items: center; padding: 6px; border-bottom: 1px solid #ddd; }
        .folder { color: #2277cc; font-weight: bold; cursor: pointer; }
        .folder:hover { text-decoration: underline; }
        .file { color: #333; }
        .op-btn { margin-left: 10px; padding: 4px 10px; cursor: pointer; }
        .upload-area { margin: 20px 0; padding: 15px; border: 2px dashed #999; }
        #msg { margin-top: 15px; font-size: 16px; font-weight: bold; }
        .success { color: green; }
        .error { color: red; }
    </style>
</head>
<body>
<div class="container">
    <h2>局域网文件管理器</h2>

    <!-- 面包屑路径导航，可点击分段跳转 -->
    <div class="path-bar" id="breadcrumb"></div>
    
    <!-- 上传区域 -->
    <div class="upload-area">
        <h4>上传文件到当前目录</h4>
        <input type="file" id="fileInput">
        <button onclick="uploadFile()" class="op-btn">确认上传</button>
    </div>

    <div id="msg"></div>
    <h4>文件列表</h4>
    <div id="fileList">
        {% for item in file_list %}
        <div class="file-item">
            {% if item.is_dir %}
                <!-- 直接点击文件夹文字进入，移除按钮 -->
                <span class="folder" onclick="enterDir('{{ item.name }}')">📁 {{ item.name }}/</span>
            {% else %}
                <span class="file">📄 {{ item.name }}</span>
                <button class="op-btn" onclick="downloadFile('{{ item.name }}')">下载</button>
            {% endif %}
        </div>
        {% endfor %}
    </div>
</div>

<script>
const currentPathRaw = "{{ current_path_relative }}";
const msgBox = document.getElementById("msg");
const breadcrumbBox = document.getElementById("breadcrumb");

// 渲染面包屑导航
function renderBreadcrumb() {
    let parts = currentPathRaw.split("/").filter(p => p.length > 0);
    let html = "";
    // 根目录点击回到首页（空path）
    html += `<span class="path-seg" onclick="jumpToPath('')">根目录</span>`;
    let tempPath = "";
    for (let seg of parts) {
        tempPath = tempPath ? `${tempPath}/${seg}` : seg;
        html += `<span class="path-split">/</span>`;
        html += `<span class="path-seg" onclick="jumpToPath('${tempPath}')">${seg}</span>`;
    }
    breadcrumbBox.innerHTML = html;
}

// 跳转到指定路径
function jumpToPath(targetPath) {
    window.location.href = `/?path=${encodeURIComponent(targetPath)}`;
}

// 进入子文件夹
function enterDir(dirName) {
    const urlParams = new URLSearchParams(window.location.search);
    const oldPath = urlParams.get("path") || "";
    let newPath;
    if(oldPath === ""){
        newPath = dirName;
    }else{
        newPath = oldPath + "/" + dirName;
    }
    jumpToPath(newPath);
}

// 【修复】直接创建下载链接，移除fetch多余判断
function downloadFile(fileName) {
    const urlParams = new URLSearchParams(window.location.search);
    const path = urlParams.get("path") || "";
    const downloadUrl = `/download?path=${encodeURIComponent(path)}&file=${encodeURIComponent(fileName)}`;
    const a = document.createElement("a");
    a.href = downloadUrl;
    a.download = fileName;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    showMsg("开始下载", true);
}

// 上传文件
async function uploadFile() {
    const fileInput = document.getElementById("fileInput");
    const file = fileInput.files[0];
    if (!file) {
        showMsg("请选择文件", false);
        return;
    }
    const urlParams = new URLSearchParams(window.location.search);
    const path = urlParams.get("path") || "";
    const formData = new FormData();
    formData.append("file", file);
    formData.append("target_path", path);
    const res = await fetch("/upload", { method: "POST", body: formData });
    const data = await res.json();
    if (data.code === 200) {
        showMsg("上传成功", true);
        setTimeout(() => window.location.reload(), 800);
    } else {
        showMsg(`失败：${data.msg}`, false);
    }
}

function showMsg(text, isSuccess) {
    msgBox.innerText = text;
    msgBox.className = isSuccess ? "success" : "error";
}

// 页面加载完成渲染面包屑
window.onload = function(){
    renderBreadcrumb();
}
</script>
</body>
</html>
"""

# 工具函数：安全拼接路径，防止路径穿越攻击
def safe_join(base, sub_path):
    target = os.path.abspath(os.path.join(base, sub_path))
    base_abs = os.path.abspath(base)
    if not target.startswith(base_abs + os.sep) and target != base_abs:
        return None
    return target

# 获取本机局域网IPv4地址
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # 连接公网DNS，获取本机出口内网IP（不会真实发包）
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# 首页：浏览目录
@app.route("/", methods=["GET"])
def index():
    sub_path = request.args.get("path", "")
    target_dir = safe_join(BASE_DIR, sub_path)
    # 路径越权/不存在
    if target_dir is None or not os.path.isdir(target_dir):
        return "目录不存在", 404

    file_list = []
    for name in os.listdir(target_dir):
        full_path = os.path.join(target_dir, name)
        file_list.append({
            "name": name,
            "is_dir": os.path.isdir(full_path)
        })
    # 区分文件夹、文件，文件夹靠前排序
    file_list.sort(key=lambda x: (0 if x["is_dir"] else 1, x["name"]))

    local_ip = get_local_ip()
    access_url = f"http://{local_ip}:{request.host.split(':')[-1]}"
    return render_template_string(
        HTML_TEMPLATE,
        current_path_relative=sub_path,  # 传给前端相对路径用于面包屑
        base_dir=BASE_DIR,
        file_list=file_list,
        access_url=access_url
    )

# 文件下载接口【修复】先校验，合法直接返回文件流，不返回json
@app.route("/download", methods=["GET"])
def download():
    sub_path = request.args.get("path", "")
    filename = request.args.get("file")
    if not filename:
        return jsonify({"code": 400, "msg": "缺少文件参数"}), 400

    folder_path = safe_join(BASE_DIR, sub_path)
    if folder_path is None:
        return jsonify({"code": 403, "msg": "非法路径"}), 403
    file_path = safe_join(folder_path, filename)
    if file_path is None:
        return jsonify({"code": 403, "msg": "禁止越权访问"}), 403
    if not os.path.isfile(file_path):
        return jsonify({"code": 404, "msg": "文件不存在"}), 404

    try:
        # 直接返回附件下载，无前置json请求
        return send_file(file_path, as_attachment=True, download_name=filename)
    except Exception as e:
        return jsonify({"code": 500, "msg": f"读取失败:{str(e)}"}), 500

# 文件上传接口
@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"code": 400, "msg": "未选择文件"}), 400
    upload_file = request.files["file"]
    target_sub = request.form.get("target_path", "")
    save_dir = safe_join(BASE_DIR, target_sub)
    if save_dir is None:
        return jsonify({"code": 403, "msg": "非法保存目录"}), 403

    save_name = secure_filename(upload_file.filename)
    save_path = safe_join(save_dir, save_name)

    if save_path is None:
        return jsonify({"code": 403, "msg": "非法文件名"}), 403

    try:
        upload_file.save(save_path)
        return jsonify({"code": 200, "msg": "success"})
    except Exception as e:
        return jsonify({"code": 500, "msg": f"写入失败:{str(e)}"}), 500


if __name__ == "__main__":
    # 命令行参数解析，默认端口8000，可自定义
    parser = argparse.ArgumentParser(description="局域网文件传输工具（无IP限制）")
    parser.add_argument("-p", "--port", type=int, default=80000, help="服务端口，默认8000")
    args = parser.parse_args()
    PORT = args.port

    LOCAL_IP = get_local_ip()
    ACCESS_URL = f"http://{LOCAL_IP}:{PORT}"

    print(f"文件服务已启动：{ACCESS_URL}")
    print(f"工作目录：{BASE_DIR}")
    # 监听0.0.0.0，无IP访问拦截
    app.run(host="0.0.0.0", port=PORT, debug=False)
