# http文件服务器程序, 可用于在本地创建一个网站，基于socket库
# 命令行：python http_file_server.py <端口号(可选)>

import sys, os, time, traceback, threading
import socket, mimetypes
from ast import literal_eval
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse,parse_qs, unquote
import chardet

HEAD_100 = b"HTTP/1.1 100 Continue\n"
HEAD_OK = b"HTTP/1.1 200 OK\n"
HEAD_206 = b"HTTP/1.1 206 Partial Content\n"
HEAD_404 = b"HTTP/1.1 404 Not Found\n"
HEAD_413 = b"HTTP/1.1 413 Payload Too Large\n"
RECV_LENGTH = 1 << 19 # sock.recv()一次接收内容的长度
CHUNK_SIZE = 1 << 20 # 发送内容长度（1MB）
SEND_SPEED = 10 # 大文件的发送速度限制，单位为MB/s，设为非正数则不限速
MAX_UPLOAD_SIZE = 1 << 26 # 64MB
MAX_FILE_SIZE = 1 << 25 # 32MB
MAX_WAIT_CONNECTIONS = 128
FLUSH_INTERVAL = 1 # 日志写入后1s刷新一次日志
HEADER_FLUSH_INTERVAL = 5

LOG_FILE=os.path.join(os.path.split(__file__)[0],"server.log")
LOG_FILE_ERR=os.path.join(os.path.split(__file__)[0],"server_err.log")
LOG_FILE_HEADER=os.path.join(os.path.split(__file__)[0],"request_headers.log")
UPLOAD_PATH=os.path.join(os.path.split(__file__)[0],"uploads")

cur_address=(None, None);log_file_reqheader=None

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
            return super().__getattr__(self,attr)
        except AttributeError:
            return getattr(self._stream,attr) # 返回self._stream的属性和方法

class RedirectedOutput:
    def __init__(self,*streams):
        if not streams:raise ValueError("At least one stream should be provided")
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

def log_addr(*args, sep=" ", file=None, flush=False): # 带时间和IP地址、端口的日志记录
    print(f"""{time.asctime()} | {cur_address[0]}:\
{cur_address[1]}{sep}{sep.join(str(arg) for arg in args)}""",
          file=file,flush=flush)


def _read_file_helper(head,file,chunk_size,start,end): # 分段读取文件使用的生成器
    yield head
    file.seek(start)
    total=0
    while total<end-start:
        size=min(chunk_size,end-start-total)
        data=file.read(size)
        total+=size
        yield data
    file.close()
def _slice_helper(data,size):
    n=len(data)
    for i in range(0,n,size):
        yield data[i:i+size]
def convert_size(num): # 将整数转换为数据单位
    units = ["", "K", "M", "G", "T", "P", "E", "Z", "Y"]

    for unit in units:
        if num < 1024:
            return f"{num:.2f}{unit}B"
        num /= 1024
    return f"{num:.2f}{units[-1]}B"

def split_formdata(data: bytes, boundary: str):
    # 分割multipart/form-data数据
    boundary = boundary.encode()
    idx = None
    wrap = b"\r\n"
    slices = []
    while idx is None or idx < len(data):
        result = data.find(boundary, idx)
        if result == -1:return
        elif idx is not None:
            slices.append((idx, result-(len(wrap)+2))) # boundary之前会加入b"\r\n--"
        idx = data.find(wrap, result+len(boundary)) + len(wrap)
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
        mimetype=mimetypes.types_map.get(os.path.splitext(path)[1],"text/plain")
    return mime_type
def check_filetype(path): # 检查文件扩展名并返回content-type
    mime_type=get_mimetype(path)
    if mime_type is None: # 未知类型
        return b"" # 不返回类型，由浏览器自行检测
    if mime_type.lower().startswith("text"):
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
            mime_type+=";charset=%s"%coding
    return b"Content-Type: %s\n"%mime_type.encode()

def parse_head(req_head): # 解析请求头中的路径和查询参数
    url = unquote(req_head.split(' ')[1])[1:] # 获取请求的路径, 在请求数据第一行
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

def get_dir_content(direc):
    path = os.path.join(os.getcwd(),direc)
    head = HEAD_OK
    response = head + f"""
<html><head>
<meta http-equiv="content-type" content="text/html;charset=utf-8">
<title>{direc} 的目录</title>
</head><body>
<h1>{direc} 的目录</h1>""".encode()
    # 获取当前路径下的各个文件、目录名
    subdirs=[] # 子目录名
    subfiles=[] # 子文件名
    for sub in os.listdir(path):
        # os.listdir()无法直接区分目录名和文件名, 因此还需进行判断
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
        size=convert_size(os.path.getsize(os.path.join(path,sub)))
        response += f'''\n<p><a href="/{direc}/{sub}">{sub}</a>\
<span style="color: #707070;">&nbsp;{size}</span></p>'''.encode()

    response += b"\n</body></html>"
    return response

def get_file(path,start=None,end=None): # 返回文件的数据
    size = os.path.getsize(path)
    if start is not None or end is not None:
        start = start or 0
        end = end or size
        head = (HEAD_206 if start>0 else HEAD_OK) + check_filetype(path)
        head += b"Content-Range: bytes %d-%d/%d\n\n" % (start,end,size)
    else:
        start = 0; end = size
        head = HEAD_OK + check_filetype(path) # 加入content-type
        # 响应头末尾以两个换行符(\n\n)结尾
        head += b"Content-Length: %d\n\n" % size # 加入文件长度
    return _read_file_helper(head,open(path,'rb'),CHUNK_SIZE,start,end) # 分段读取文件

def getcontent(direc,query=None,fragment=None,start=None,end=None): # 根据url的路径direc构造响应数据
    if query is None:
        query = {}

    # 将direc转换为系统路径, 放入path
    path = os.path.join(os.getcwd(),direc)
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
            response = get_dir_content(direc)

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
        response = HEAD_404 + f"""
<html><head>
<meta http-equiv="content-type" content="text/html;charset=utf-8">
<title>404</title>
</head><body>
<h1>404 Not Found</h1>
<p>页面 /{direc} 未找到</p>
<a href="/{direc}/..">返回上一级</a>
<a href="/">返回首页</a>
</body></html>
""".encode()
    return response

def send_response(sock,response,address):
    # 分段发送响应
    if isinstance(response,bytes):
        response = _slice_helper(response,CHUNK_SIZE)
    total=0
    chunk=next(response)
    sock.send(chunk)
    begin=time.perf_counter()
    while True:
        size=len(chunk)
        total+=size
        try:
            chunk=next(response)
        except StopIteration:
            break
        else:
            if SEND_SPEED > 0:
                seconds = (total/(1<<20))/SEND_SPEED - \
                          (time.perf_counter() - begin) # 预计时间 - 实际时间
                if seconds > 0:
                    time.sleep(seconds) # 延迟发送，限制速度
        sock.send(chunk)
    if SEND_SPEED > 0 and total >= SEND_SPEED*(1<<20) \
        or SEND_SPEED <= 0 and total >= 1<<27: # 如果预计发送时间超过1秒，或不限速时大于128MB
        log_addr("较大响应 (%s) 发送完毕" % convert_size(total))

def handle_post(sock,req_head,req_info,content):
    template = """
<html><head>
<meta http-equiv="content-type" content="text/html;charset=utf-8">
<title>{title}</title>
</head><body>
<h1>{msg}</h1>
<a href="javascript:void(0);"
onclick="window.history.back();">返回</a>
</body></html>
""" # 提交完成的页面模板

    length = int(req_info.get('Content-Length',-1))
    if length > MAX_UPLOAD_SIZE:
        log_addr("尝试提交过大表单:",convert_size(MAX_UPLOAD_SIZE))
        msg = f"提交失败，数据量大于 {convert_size(MAX_UPLOAD_SIZE)} "
        # TODO: 会导致客户端浏览器显示“已重置连接”
        return HEAD_413 + template.format(title="提交失败",msg=msg).encode()
    content_type, formdata_info = parse_line(req_info["Content-Type"])
    is_multipart_form = content_type == "multipart/form-data"

    if len(content) < length: # 内容不完整，尝试继续接收数据
        chunks = []
        received_len = len(content)
        while True:
            new_data = sock.recv(RECV_LENGTH)
            chunks.append(new_data)
            received_len += len(new_data)
            if not new_data or received_len >= length:break
            if received_len > MAX_UPLOAD_SIZE:return HEAD_413 + b"\n"
        content += b"".join(chunks)

    if length != -1:content = content[:length] # 截断过长的数据

    if is_multipart_form: # 处理多部分表单，如上传文件等请求
        form = {}
        for data in split_formdata(content, formdata_info["boundary"]):
            _, info = get_request_info(data, has_head = False)
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
                    msg = f"提交失败，最大只能上传 {convert_size(MAX_FILE_SIZE)} 的文件"
                    return HEAD_413 + template.format(title=title,msg=msg).encode()

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
            return HEAD_100 # 让客户端继续发送数据
        else:
            form=parse_qs(content.decode("utf-8"),
                          keep_blank_values=True,encoding="utf-8")

    log_addr("提交数据:",form)

    title = msg = "提交成功"
    return HEAD_OK + template.format(title=title,msg=msg).encode()

def get_request_info(data: bytes, has_head = True):
    # 获取请求头部信息，首行存入req_head字符串，其他信息存入字典req_info
    lines = data.splitlines()
    if has_head:
        req_head = lines.pop(0).decode("utf-8")
    else:
        req_head = None

    req_info = {}
    for line in lines:
        if not line:break # 两个空行表示开头的结束
        line = line.decode("utf-8")
        lst = line.split(':', 1)
        try:
            key, value = lst[0].strip(), lst[1].strip()
            req_info[key] = value
        except (ValueError, IndexError): # 不是请求头信息时
            pass
    return req_head,req_info

def handle_get(req_head,req_info):
    url=unquote(req_head.split(' ')[1])
    direc,query,fragment=parse_head(req_head)
    if "Range" in req_info: # 断点续传
        range_=req_info["Range"].split("=",1)[1]
        start,end=range_.split("-")
        start = int(start) if start else None
        end = int(end) if end else None
        log_addr("访问URL: %s (从 %s 到 %s 断点续传)" % (url,
            convert_size(start) if start is not None else None,
            convert_size(end) if end is not None else "末尾"))
        return getcontent(direc,query,fragment,start,end)
    else:
        log_addr("访问URL:",url)
        return getcontent(direc,query,fragment) # 获取目录的数据

def handle_client(sock, address):# 处理客户端请求
    try:raw = sock.recv(RECV_LENGTH)
    except ConnectionError as err:
        log_addr("连接异常 (%s): %s" % (type(err).__name__,str(err)))
        return
    if not raw:return # 忽略空数据

    req_head,req_info = get_request_info(raw)
    log_addr(f"{req_head!r} {req_info}", file=log_file_reqheader) # 记录请求头

     # 获取响应数据，response可以为bytes类型，或一个生成器
    if req_head.startswith("POST"): # POST请求
        response=handle_post(sock,req_head,req_info,raw.splitlines()[-1])
    else: # GET请求
        response=handle_get(req_head,req_info)

    try:send_response(sock,response,address) # 向客户端分段发送响应数据
    except ConnectionError as err:
        log_addr("连接异常 (%s): %s" % (type(err).__name__,str(err)))
    sock.close() # 关闭客户端连接

def handle_client_thread(*args,**kw): # 仅用于多线程中产生异常时输出错误信息
    try:handle_client(*args,**kw)
    except Exception:
        traceback.print_exc()

def main():
    global cur_address, log_file_reqheader
    log_file=AutoFlushWrapper(open(LOG_FILE,"a",encoding="utf-8"),FLUSH_INTERVAL)
    log_file.write("\n") # 插入空行，分割上次的日志
    sys.stdout=RedirectedOutput(log_file,sys.stdout) # 重定向输出
    log_file_err=AutoFlushWrapper(open(LOG_FILE_ERR,"a",encoding="utf-8"),
                                  FLUSH_INTERVAL)
    log_file_err.write(f"\n{time.asctime()}:\n")
    sys.stderr=RedirectedOutput(log_file_err,sys.stderr)
    log_file_reqheader=AutoFlushWrapper(open(LOG_FILE_HEADER,"a",encoding="utf-8"),
                                     HEADER_FLUSH_INTERVAL) # 记录请求头的日志

    host = socket.gethostname()
    port=int(sys.argv[1]) if len(sys.argv)==2 else 80 # 80为HTTP的默认端口
    ips = socket.gethostbyname_ex(host)[2] # 或者socket.gethostbyname(host)
    print(f"已在 {time.asctime()} 启动服务器")
    print("服务器的IP:",ips)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("", port))
    sock.listen(MAX_WAIT_CONNECTIONS) # 监听

    # 单线程模式，一次处理一个客户端
    #while True:
    #    client_sock, cur_address = sock.accept()
    #    handle_client(client_sock, cur_address)
    # 多线程
    with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
        try:
            while True:
                client_sock, cur_address = sock.accept()
                executor.submit(handle_client_thread, client_sock, cur_address)
        finally:
            sock.close()
            sys.stdout.flush();sys.stderr.flush()
            log_file_reqheader.flush()

if __name__ == "__main__":main()
