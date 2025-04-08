**The English documentation is shown below the Chinese version.**

这是一个轻量级的Python HTTP文件服务器，用于取代python自带的`http.server`模块，基于`socket`模块提供的TCP/IP实现了HTTP协议。  
支持目录显示、文件下载、断点续传和下载限速、表单提交和文件上传，以及`Content-Type`和编码检测等功能，适合搭建一个小型网站。  
服务器还应用`mimetypes`库，自动根据扩展名判断文件的`Content-Type`，并基于`chardetect`库自动检测文件编码；  
此外服务器实现了分块发送响应数据，实现了限制下载速度和断点续传功能，适合传输大文件，并能处理POST请求提交的表单和文件。  

## 用法

1. **启动服务器**：
   运行程序时，需要提供端口号作为命令行参数。例如：
   ```bash
   python http_file_server.py <可选的端口号，如8080>
   ```
   这将启动一个监听于8080端口的HTTP文件服务器，可通过`127.0.0.1:8080`访问。如果没有指定端口号，默认为HTTP使用的80。

2. **访问文件**：
   服务器的根目录默认为当前的**工作路径**，例如访问：
   ```
   http://127.0.0.1/path/index.html
   ```
   将会访问到文件`<工作路径>/path/index.html`。  
   `.htm`或`.html`文件的扩展名可以省略。如`127.0.0.1/path/index`和`127.0.0.1/path/index.html`是一样的。  
   另外，如果目录中有名为`index`的文件（无论扩展名为何），文件名也可省略，如`127.0.0.1/path`和`127.0.0.1/path/index.css`相同。  

3. **日志记录**：
   服务器日志默认记录在`http_file_server.py`同一目录下的`server.log`和`server_err.log`中，并支持延迟刷新(flush)，确保服务器意外停止不会导致日志丢失，同时避免了每次写入都刷新引发的性能问题。  

## 功能

1. **断点续传和下载限速**：
   服务器实现了分块发送响应数据，使得服务器能限制下载速度，并支持断点续传。

2. **处理POST表单/文件提交**：
   服务器实现了处理POST请求，支持用户通过表单提交数据，以及上传文件。

3. **自动检测Content-Type**：
   服务器使用 `mimetypes` 库自动判断文件的内容类型。客户端在请求文件时，服务器会根据文件的扩展名来设置 `Content-Type` 头。

4. **自动编码检测**：
   服务器应用 `chardet` 库来自动检测文件编码，避免了浏览器的乱码问题。

5. **安全性**：
   服务器能检测`..`等常见的上级目录攻击格式，无法访问当前工作路径之外上级目录的任何文件，避免了目录遍历攻击。

---

This is a lightweight Python HTTP file server designed to replace Python's built-in `http.server` module. It implements the HTTP protocol entirely based on the TCP/IP stack provided by the `socket` module.  
The server supports directory listing, file downloading, resume downloads (range requests), download speed limiting, form submission, and file uploads. It also provides `Content-Type` detection and character encoding detection, making it suitable for setting up a small website.  
Additionally, the server uses the `mimetypes` library to automatically determine a file's `Content-Type` based on its extension and leverages the `chardetect` library to automatically detect file encoding.  
The server implements chunked data transmission to support download speed limiting and resume downloads, making it ideal for transferring large files. It can also handle POST requests for form submissions and file uploads.

## Usage

1. **Starting the Server**:
   When running the program, you need to provide the port number as a command-line argument. For example:
   ```bash
   python http_file_server.py <optional port number, e.g., 8080>
   ```
   This will start an HTTP file server listening on port 8080, accessible via `127.0.0.1:8080`. If no port number is specified, the default HTTP port (80) will be used.

2. **Accessing Files**:
   The server's root directory defaults to the current **working directory**. For example, accessing:
   ```
   http://127.0.0.1/path/index.html
   ```
   will retrieve the file `<working directory>/path/index.html`.  
   The `.htm` or `.html` file extensions can be omitted. For instance, `127.0.0.1/path/index` and `127.0.0.1/path/index.html` are equivalent.  
   Additionally, if a directory contains a file named `index` (regardless of its extension), the file name can also be omitted. For example, `127.0.0.1/path` and `127.0.0.1/path/index.css` are treated the same.

3. **Logging**:
   By default, server logs are stored in the same directory as `http_file_server.py` under `server.log` and `server_err.log`. The server supports delayed flushing to ensure that logs are not lost in case of an unexpected shutdown, while also avoiding performance issues caused by frequent flushes.

## Features

1. **Resume Downloads and Speed Limiting**:
   The server implements chunked data transmission, enabling it to limit download speed and support resume downloads.

2. **Handling POST Form/File Submissions**:
   The server supports handling POST requests, allowing users to submit data through forms and upload files.

3. **Automatic Content-Type Detection**:
   The server uses the `mimetypes` library to automatically determine the file's content type. When a client requests a file, the server sets the appropriate `Content-Type` header based on the file's extension.

4. **Automatic Encoding Detection**:
   The server uses the `chardet` library to automatically detect file encoding, preventing issues with garbled text in the browser.

5. **Security**:
   The server detects common directory traversal attacks (e.g., `..`) and prevents access to any files outside the current working directory, mitigating directory traversal vulnerabilities.
