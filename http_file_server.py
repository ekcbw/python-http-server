#!/usr/bin/env python3
# http文件服务端，基于socket库
# 命令行：python http_file_server.py [选项] [端口号(可选)]
import sys, os, time, traceback, argparse, threading
import socket, mimetypes
from typing import Iterator, Dict
from ast import literal_eval
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse, parse_qs, unquote
import chardet

STATUS_100 = b"HTTP/1.1 100 Continue\r\n"
STATUS_OK = b"HTTP/1.1 200 OK\r\n"
STATUS_206 = b"HTTP/1.1 206 Partial Content\r\n"
STATUS_404 = b"HTTP/1.1 404 Not Found\r\n"
STATUS_413 = b"HTTP/1.1 413 Payload Too Large\r\n"
STATUS_ACCEPT_RANGES = b"Accept-Ranges: bytes\r\n"
RECV_LENGTH = 1 << 19 # sock.recv()一次接收内容的长度
CHUNK_SIZE = 1 << 20 # 发送内容长度（1MB）
SEND_SPEED = 10 # 大文件的发送速度限制，单位为MB/s，设为非正数则不限速
MAX_UPLOAD_SIZE = 1 << 26 # 64MB
MAX_FILE_SIZE = 1 << 25 # 32MB
MAX_WAITING_CONNECTIONS = 256
FLUSH_INTERVAL = 1 # 日志写入后1s刷新一次日志
HEADER_FLUSH_INTERVAL = 5
MAX_WORKERS = 128 # 最大线程数
TIMEOUT = 10 # 超时

LOG_PATH = os.path.join(os.path.split(__file__)[0],"logs")
LOG_FILE = os.path.join(LOG_PATH,"server.log")
LOG_FILE_ERR = os.path.join(LOG_PATH,"server_err.log")
LOG_FILE_HEADS = os.path.join(LOG_PATH,"request_heads.log")
UPLOAD_PATH = os.path.join(os.path.split(__file__)[0],"uploads")

_cur_address=threading.local();_log_file_reqhead=None
_send_speed=SEND_SPEED;_server_root_dir=os.getcwd()

class AutoFlushWrapper: # 自动调用flush()的包装器
    def __init__(self,stream,interval=0):
        self._stream=stream
        self._interval=interval
        self._waiting_for_flush=False # 是否正在等待调用flush

        self._condition=threading.Condition()
        self._stopped=threading.Event()
        flush_thread=threading.Thread(target=self._auto_flush_thread)
        flush_thread.daemon=True
        flush_thread.start()

    def write(self,message):
        result=self._stream.write(message)
        if not self._waiting_for_flush:
            with self._condition:
                self._condition.notify_all()
        return result
    def _auto_flush_thread(self): # 线程，自动调用flush()
        while True:
            with self._condition:
                self._condition.wait()
            if self._stopped.is_set():
                break

            self._waiting_for_flush=True

            time.sleep(self._interval)
            self._stream.flush()

            self._waiting_for_flush=False
    def stop_auto_flush(self):
        if self._stopped.is_set(): # 已经停止过
            return
        self._stopped.set()
        with self._condition:
            self._condition.notify_all()
    def close(self):
        self.stop_auto_flush()
        self._stream.close() # close()会自动调用flush()
    def __getattr__(self,attr):
        try:
            return getattr(super(), attr)
        except AttributeError:
            return getattr(self._stream,attr) # 返回self._stream的属性和方法

class RedirectedOutput:
    def __init__(self,*streams):
        if not streams:
            raise ValueError("At least one stream should be provided")
        self._streams=streams
    def write(self,data):
        written=self._streams[0].write(data)
        result=written if written is not None else len(data)
        for stream in self._streams[1:]:
            written=stream.write(data)
            result=min(result,written if written is not None else result)
        return result
    def flush(self):
        for stream in self._streams:
            stream.flush()
    def stop_auto_flush(self):
        for stream in self._streams:
            if hasattr(stream, "stop_auto_flush"):
                stream.stop_auto_flush()
    def isatty(self):
        return any(stream.isatty() for stream in self._streams)
    def close(self):
        for stream in self._streams:
            stream.close()

class Response:
    body: bytes|Iterator[bytes]
    def __init__(self, status, head: Dict[str, str] = {},
                 body: bytes|Iterator[bytes] = b"", chunk_size = CHUNK_SIZE):
        self.status = status
        self.head = head
        self.body = body
        self.chunk_size = chunk_size
    def iter(self) -> Iterator[bytes]:
        if isinstance(self.body, bytes) and "Content-Length" not in self.head:
            self.head["Content-Length"] = str(len(self.body)) # 自动添加内容长度
        head = self.status + b"\r\n".join(key.encode() + b": " + value.encode()
                                for key, value in self.head.items()) + b"\r\n"
        if isinstance(self.body, bytes):
            yield from  _slice_helper(head + b"\r\n" + self.body, self.chunk_size)
        else:
            yield head + b"\r\n"
            yield from self.body

def _read_file_helper(file,chunk_size,start,end) -> Iterator[bytes]:
    # 分段读取文件的生成器，也负责关闭文件
    file.seek(start)
    total=0
    while total<end-start:
        size=min(chunk_size,end-start-total)
        data=file.read(size)
        total+=size
        yield data
    file.close()
def _slice_helper(data:bytes,size) -> Iterator[bytes]:
    n=len(data)
    for i in range(0,n,size):
        yield data[i:i+size]

def log_addr(*args, sep=" ", file=None, flush=False): # 带时间和IP地址、端口的日志记录
    addr = getattr(_cur_address,"addr",(None,None))
    print(f"""{time.asctime()} | [{addr[0]}]:{addr[1]}\
{sep}{sep.join(str(arg) for arg in args)}""",
          file=file,flush=flush)

def convert_size(num): # 将整数转换为数据单位
    units = ["", "K", "M", "G", "T", "P", "E", "Z", "Y"]

    for unit in units:
        if num < 1024:
            return f"{num:.2f}{unit}B"
        num /= 1024
    return f"{num:.2f}{units[-1]}B"

def parse_range(range_): # 解析Range字段
    range_=range_.split("=",1)[1]
    start,end=range_.split("-")
    start = int(start) if start else None
    end = int(end)+1 if end else None
    return start, end

def split_formdata(data: bytes, boundary: str):
    # 分割multipart/form-data数据
    bound = boundary.encode()
    idx = None
    wrap = b"\r\n"
    slices = []
    while idx is None or idx < len(data):
        result = data.find(bound, idx)
        if result == -1:return
        elif idx is not None:
            slices.append((idx, result-(len(wrap)+2))) # boundary之前会加入b"\r\n--"
        idx = data.find(wrap, result+len(bound)) + len(wrap)
    for item in slices:
        yield data[item[0]:item[1]]

def parse_line(line, use_eval = False):
    # 辅助函数，解析类似form-data; name="file"的数据
    result = {}; type_ = None
    for i,item in enumerate(line.split(";")):
        item = item.strip()
        lst = item.split("=",1) # 解析字符串
        if len(lst) < 2:
            if i == 0: type_ = item
            continue
        value = lst[1]
        if use_eval:value = literal_eval(value)
        result[lst[0]] = value
    return type_, result

def get_mimetype(path):
    mimetypes.types_map[".js"]="application/javascript"
    mime_type=mimetypes.guess_type(path)[0]
    if mime_type=="text/plain":
        mime_type=mimetypes.types_map.get(os.path.splitext(path)[1],"text/plain")
    return mime_type
def check_content_type(path) -> str | None: # 检查文件扩展名并返回content-type
    mime_type = get_mimetype(path)
    if mime_type is not None and mime_type.lower().startswith("text"):
        with open(path,"rb") as f:
            head=f.read(512) # 读取文件头部，并检测编码
            detected=chardet.detect(head)
            coding=detected["encoding"]
            if coding=="ascii": # 如果未检测到多字节的编码，则尝试继续检测
                data=f.read(3072)
                if data:
                    detected=chardet.detect(data)
                    coding=detected["encoding"]
            if coding=="ascii":
                coding="utf-8" # 默认使用utf-8
        if coding is not None and detected["confidence"]>0.9:
            mime_type+=f";charset={coding}"
    return mime_type

def parse_head(req_line): # 解析请求头中的路径和查询参数
    url = unquote(req_line.split(' ')[1])[1:] # 获取请求的路径, 在请求数据第一行
    parse_result = urlparse(url)
    direc,query_str,fragment = parse_result.path,\
        parse_result.query,parse_result.fragment
    query = parse_qs(query_str,keep_blank_values=True)
    fragment = fragment or None
    if direc == "": # 路径为空，则用当前路径
        direc="."
    direc=direc.replace("\\","/")
    if direc[-1]=="/": # 去除末尾多余的斜杠
        direc=direc[:-1]
    return direc,query,fragment

def list_dir(direc) -> Response:
    path = os.path.join(_server_root_dir, direc)
    response = f"""\
<!DOCTYPE html><html><head>
<meta http-equiv="content-type" content="text/html;charset=utf-8">
<title>{direc} 的目录</title>
</head><body>
<h1>{direc} 的目录</h1>""".encode()
    # 获取当前路径下的各个文件、目录名
    subdirs=[] # 子目录名
    subfiles=[] # 子文件名
    for sub in os.listdir(path):
        if os.path.isfile(os.path.join(path,sub)): # 如果子项是文件
            subfiles.append(sub)
        else: # 子项是目录
            subdirs.append(sub)
    subdirs.sort(key=lambda s:s.lower()) # 升序排序
    subfiles.sort(key=lambda s:s.lower())

    if direc != ".":
        response += f'\n<p><a href="/{direc}/..">[上级目录]</a></p>'.encode()
    # 依次显示各个子文件、目录
    for sub in subdirs:
        response += f'\n<p><a href="/{direc}/{sub}">[{sub}]</a></p>'.encode()
    for sub in subfiles:
        size = convert_size(os.path.getsize(os.path.join(path,sub)))
        response += f'''\n<p><a href="/{direc}/{sub}">{sub}</a>\
<span style="color: #707070;">&nbsp;{size}</span></p>'''.encode()

    response += b"\n</body></html>"
    return Response(STATUS_OK, {}, response)

def get_file(path,start=None,end=None) -> Response: # 返回文件的数据
    size = os.path.getsize(path)
    if start is not None or end is not None:
        start = start or 0
        end = min(end, size) if end is not None else size # end变量为不包含
        length = end - start
    else:
        start = 0; end = length = size
    content_type = check_content_type(path)

    status = STATUS_206 if start > 0 else STATUS_OK
    head = {"Accept-Ranges": "bytes",
            "Content-Length": str(length), # 加入文件长度
            "Content-Range": f"bytes {start}-{end-1}/{size}"}
    if content_type is not None:
        head["Content-Type"] = content_type # 加入content-type
    body = _read_file_helper(open(path,'rb'),CHUNK_SIZE,start,end) # 分段读取文件
    return Response(status, head, body)

def getcontent(direc,query=None,fragment=None,start=None,end=None) -> Response: # 根据url的路径direc构造响应数据
    if query is None:
        query = {}

    # 将direc转换为系统路径, 放入path
    path = os.path.join(_server_root_dir,direc)
    try:
        if ".." in direc.split("/"): # 禁止访问上层目录
            raise OSError # 引发错误, 进入except语句
        if os.path.isdir(path):
            # 找出路径中名为index的文件，若有则直接读取
            file=None
            for f in os.listdir(path):
                if f.split(".")[0].lower()=="index":
                    file = f
                    if f.split(".")[-1].lower() in ("htm","html"): # 当有多个index文件时html文件优先
                        break
            if file is not None:
                path = os.path.join(path,file)

        # 构造响应数据
        if os.path.isfile(path): # --path是文件, 就打开文件并读取--
            response = get_file(path,start,end)

        elif os.path.isdir(path): # --path是路径, 就显示路径中的各个文件--
            response = list_dir(direc)

        else: # 不存在文件或目录
            # 若.html的后缀名省略，自动寻找html文件
            # 不过，例如要访问path，path/index.html要优先于path.html，用户可自行修改
            for ext in (".htm",".html"):
                file = path + ext
                if os.path.isfile(file):
                    response = get_file(file,start,end)
                    break
            else:
                raise OSError # 当作错误处理, 进入except语句

    except OSError:
        # 返回404
        response = Response(STATUS_404, {}, f"""\
<!DOCTYPE html><html><head>
<meta http-equiv="content-type" content="text/html;charset=utf-8">
<title>404</title>
</head><body>
<h1>404 Not Found</h1>
<p>页面 /{direc} 未找到</p>
<a href="/{direc}/..">返回上一级</a>
<a href="/">返回首页</a>
</body></html>
""".encode())
    return response

def send_response(sock, response: Response):
    resp = response.iter()
    # 分段发送响应
    total=0; chunk=next(resp)
    sock.sendall(chunk)
    begin=time.perf_counter()
    while True:
        size=len(chunk)
        total+=size
        try:
            chunk=next(resp)
        except StopIteration:
            break
        else:
            if _send_speed > 0:
                seconds = (total/(1<<20))/_send_speed - \
                          (time.perf_counter() - begin) # 预计时间 - 实际时间
                if seconds > 0:
                    time.sleep(seconds) # 延迟发送，限制速度
        sock.sendall(chunk)
    if _send_speed > 0 and total >= _send_speed*(1<<20) \
        or _send_speed <= 0 and total >= 1<<27: # 如果预计发送时间超过1秒，或不限速时大于128MB
        log_addr("较大响应 (%s) 发送完毕" % convert_size(total))

def handle_post(sock,req_head,content) -> Response:
    template = """
<!DOCTYPE html><html><head>
<meta http-equiv="content-type" content="text/html;charset=utf-8">
<title>{title}</title>
</head><body>
<h1>{msg}</h1>
<a href="javascript:void(0);"
onclick="window.history.back();">返回</a>
</body></html>
""" # 提交完成的页面模板

    length = int(req_head.get('Content-Length',-1))
    if length > MAX_UPLOAD_SIZE:
        log_addr("尝试提交过大表单:",convert_size(MAX_UPLOAD_SIZE))
        msg = f"提交失败，数据量大于 {convert_size(MAX_UPLOAD_SIZE)} "
        # TODO: 会导致客户端浏览器显示“已重置连接”
        return Response(STATUS_413, {}, template.format(title="提交失败",msg=msg).encode())
    content_type, formdata_info = parse_line(req_head["Content-Type"])
    is_multipart_form = content_type == "multipart/form-data"

    if len(content) < length: # 内容不完整，尝试继续接收数据
        chunks = []
        received_len = len(content)
        while True:
            new_data = sock.recv(RECV_LENGTH)
            chunks.append(new_data)
            received_len += len(new_data)
            if not new_data or received_len >= length:break
            if received_len > MAX_UPLOAD_SIZE:
                return Response(STATUS_413)
        content += b"".join(chunks)

    if length != -1:content = content[:length] # 截断过长的数据

    if is_multipart_form: # 处理多部分表单，如上传文件等请求
        form = {}
        for data in split_formdata(content, formdata_info["boundary"]):
            _, info = get_request_info(data, include_req_line = False)
            # Content-Disposition类似于: form-data; name="file"; filename="\xe5\x9b\xbe.jpg"
            content_type, disposition = parse_line(info["Content-Disposition"], use_eval=True)
            idx = data.find(b"\r\n\r\n")
            if idx == -1:data=b""
            data = data[idx + 4:] # 内容数据

            if "filename" in disposition:
                os.makedirs(UPLOAD_PATH,exist_ok=True)
                if len(data) > MAX_FILE_SIZE:
                    log_addr("尝试提交过大的文件:",disposition["filename"],
                             convert_size(len(data)))
                    title = "提交失败"
                    msg = f"提交失败，最大仅允许 {convert_size(MAX_FILE_SIZE)} 的文件"
                    return Response(STATUS_413, {}, template.format(title=title,msg=msg).encode())
                if "/" in disposition["filename"] or "\\" in disposition["filename"]:
                    log_addr("无效路径:",disposition["filename"])
                    return Response(STATUS_413)

                filename = os.path.join(UPLOAD_PATH,disposition["filename"])
                with open(filename,"wb") as f:
                    f.write(data) # 保存上传的文件
                log_addr("上传文件:",disposition["filename"])
                form[disposition["name"]] = filename
            else:
                try: data = data.decode()
                except UnicodeDecodeError: pass
                form[disposition["name"]] = data

    else:
        if len(content)<length: # post含有多个tcp数据包时
            return Response(STATUS_100) # 让客户端继续发送数据
        else:
            form = parse_qs(content.decode("utf-8"),
                            keep_blank_values=True,encoding="utf-8")

    log_addr("提交数据:",form)

    title = msg = "提交成功"
    return Response(STATUS_OK, {}, template.format(title=title,msg=msg).encode())

def get_request_info(data: bytes, include_req_line = True):
    # 获取请求头部信息，首行存入req_line字符串，其他信息存入字典req_head
    lines = data.splitlines()
    if include_req_line:
        req_line = lines.pop(0).decode("utf-8", errors="backslashreplace")
    else:
        req_line = None

    req_head = {}
    for line in lines:
        if not line:break # 两个空行表示开头的结束
        line = line.decode("utf-8", errors="backslashreplace")
        lst = line.split(':', 1)
        try:
            key, value = lst[0].strip(), lst[1].strip()
            req_head[key] = value
        except (ValueError, IndexError): # 不是请求头信息时
            pass
    return req_line,req_head

def handle_get(req_line,req_head):
    url=unquote(req_line.split(' ')[1])
    direc,query,fragment=parse_head(req_line)
    if "Range" in req_head: # 断点续传
        start, end = parse_range(req_head["Range"])
        log_addr("访问URL: {} (从 {} 到 {} 断点续传)".format(url,
            convert_size(start) if start is not None else None,
            convert_size(end) if end is not None else "末尾"))
        return getcontent(direc,query,fragment,start,end) # end索引为包含
    else:
        log_addr("访问URL:",url)
        return getcontent(direc,query,fragment) # 获取目录的数据

def handle_client(sock: socket.socket):# 处理客户端请求
    sock.settimeout(TIMEOUT)
    keep_alive = True
    
    while keep_alive:
        try:
            raw = sock.recv(RECV_LENGTH)
        except (ConnectionError, TimeoutError) as err:
            log_addr(f"连接异常 ({type(err).__name__}): {err}")
            break
        if not raw: break # 忽略空数据

        req_line,req_head = get_request_info(raw)
        if req_head.get("Connection", "").lower() == 'close':
            keep_alive = False
        log_addr(f"{req_line!r} {req_head}", file=_log_file_reqhead) # 记录请求头

        # 获取响应数据，response可以为bytes类型，或一个生成器
        if raw.startswith(b"POST"): # POST请求
            response = handle_post(sock,req_head,raw.splitlines()[-1])
        else: # GET请求
            response = handle_get(req_line,req_head)

        try:
            if keep_alive:
                response.head.update({"Connection": "keep-alive",
                                      "Keep-Alive": f"timeout={TIMEOUT}"})
            send_response(sock, response) # 向客户端分段发送响应数据
        except (ConnectionError, TimeoutError) as err:
            log_addr(f"连接异常 ({type(err).__name__}): {err}")
            break

def handle_client_thread(sock, address): # 仅用于发生异常时输出错误信息
    _cur_address.addr = address
    try:
        handle_client(sock)
    except Exception:
        traceback.print_exc()
    finally:
        try:sock.close()
        except Exception:pass

def main():
    global _log_file_reqhead, _send_speed, _server_root_dir

    parser = argparse.ArgumentParser()
    parser.add_argument('--root-dir')
    parser.add_argument('--disable-all-logs', action='store_true')
    parser.add_argument('--disable-request-header-log', action='store_true')
    parser.add_argument('--send-speed', type=int)
    parser.add_argument('port', nargs='?', type=int)
    args = parser.parse_args()
    _server_root_dir = args.root_dir or os.getcwd()
    if args.send_speed is not None:
        _send_speed = args.send_speed
    if not args.disable_all_logs:
        os.makedirs(LOG_PATH,exist_ok=True)
        log_file = AutoFlushWrapper(open(LOG_FILE,"a",encoding="utf-8"),FLUSH_INTERVAL)
        log_file.write("\n") # 插入空行，分割上次的日志
        sys.stdout = RedirectedOutput(log_file,sys.stdout) # 重定向输出
        log_file_err = AutoFlushWrapper(open(LOG_FILE_ERR,"a",encoding="utf-8"),
                                        FLUSH_INTERVAL)
        log_file_err.write(f"\n{time.asctime()}:\n")
        sys.stderr = RedirectedOutput(log_file_err,sys.stderr)
        if not args.disable_request_header_log:
            _log_file_reqhead = AutoFlushWrapper(
                open(LOG_FILE_HEADS,"a",encoding="utf-8"),
                HEADER_FLUSH_INTERVAL) # 记录请求头的日志

    host = socket.gethostname()
    port = args.port if args.port is not None else 80 # 80为HTTP的默认端口
    ips = socket.gethostbyname_ex(host)[2] # 或socket.gethostbyname(host)
    print(f"已在 {time.asctime()} 启动服务端")
    print("服务端的IP:", ips)

    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    sock.bind(("", port))
    sock.listen(MAX_WAITING_CONNECTIONS) # 监听

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        try:
            while True:
                client_sock, address = sock.accept()
                executor.submit(handle_client_thread, client_sock, address)
        except KeyboardInterrupt:
            print("已停止服务端")
        finally:
            sys.stdout.flush();sys.stderr.flush()
            if _log_file_reqhead is not None:
                _log_file_reqhead.flush()
            sock.close()

if __name__ == "__main__":main()
