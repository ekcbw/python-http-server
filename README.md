**The English documentation is shown below the Chinese version.**

这是一个Python的轻量级HTTP文件服务器，可以取代python自带的`http.server`模块，基于`socket`模块实现。
服务器基于`mimetypes`库，自动根据扩展名判断文件的类型`content-type`，并基于`chardetect`库自动检测编码；
此外服务器实现了分块发送响应数据，实现了限制下载速度和断点续传功能，适合传输大文件，并能处理POST请求提交的表单。

## 用法

1. **启动服务器**：
   运行程序时，需要提供端口号作为命令行参数。例如：
   ```bash
   python http_file_server.py <可选的端口号，如8080>
   ```
   这将启动一个在8080端口监听的HTTP文件服务器。如果没有指定端口号，端口默认为HTTP使用的80。

2. **访问文件**：
   启动服务器后，可以通过浏览器访问当前工作目录中的文件，例如：
   ```
   http://127.0.0.1/path/index.html
   ```
   `.htm`或`.html`文件的扩展名可以省略。如`127.0.0.1/path/index`和`127.0.0.1/path/index.html`是一样的。
   此外，如果文件夹下有名为`index`的文件（无论扩展名为何），文件名也是可以省略的。
   如`127.0.0.1/path`和`127.0.0.1/path/index.css`也是相同的。

3. **日志记录**：
   可以将服务器的输出日志重定向到文件以记录日志，例如：
   ```bash
   python http_file_server.py 8080 > server_log.txt
   ```
   这将把所有的日志记录到 `server_log.txt` 文件中。

## 功能

1. **基于扩展名的Content-Type**：
   该服务器使用 `mimetypes` 库自动判断文件的内容类型。这意味着客户端在请求文件时，服务器会根据文件的扩展名来设置 `Content-Type` 头。

2. **自动编码检测**：
   服务器使用 `chardet` 库来自动检测文件的编码格式。这对于处理各种文本文件非常有用，可以确保正确读取和返回文件内容。

3. **分块发送响应数据**：
   服务器实现了分块发送功能，这使得它能够限制下载速度和支持断点续传。这对于共享大文件非常实用，可以提高用户的体验。

4. **处理POST请求**：
   该服务器具备处理POST请求的基本功能。这意味着用户可以通过表单提交数据，服务器能够正确接收并处理这些请求。


This is a lightweight HTTP file server implemented in Python, serving as a replacement for the built-in `http.server` module and based on the `socket` module.
The server utilizes the `mimetypes` library to automatically determine the file's content type based on its file extension, and uses the `chardet` library for automatic encoding detection.
In addition, the server implements chunked response data transfer, enabling download speed limits and resume capabilities, making it suitable for large file transfers. It can also handle POST requests submitted by forms.

## Usage

1. **Starting the Server**:
   When running the program, you need to provide a port number as a command line argument. For example:
   ```bash
   python http_file_server.py <optional port number, e.g., 8080>
   ```
   This will start an HTTP file server listening on port 8080. If no port number is specified, the default port will be 80 for HTTP.

2. **Accessing Files**:
   After starting the server, you can access files in the current working directory via a browser, for example:
   ```
   http://127.0.0.1/path/index.html
   ```
   The extensions `.htm` or `.html` can be omitted. For instance, `127.0.0.1/path/index` and `127.0.0.1/path/index.html` are equivalent.
   Additionally, if there is a file named `index` in a folder (regardless of the extension), the filename can also be omitted.
   For example, `127.0.0.1/path` and `127.0.0.1/path/index.css` are the same.

3. **Logging**:
   You can redirect the server's output logs to a file for logging purposes, for example:
   ```bash
   python http_file_server.py 8080 > server_log.txt
   ```
   This will log all output to the `server_log.txt` file.

## Features

1. **Content-Type Based on Extension**:
   The server uses the `mimetypes` library to automatically determine the content type of files. This means that when a client requests a file, the server sets the `Content-Type` header based on the file's extension.

2. **Automatic Encoding Detection**:
   The server uses the `chardet` library for automatic detection of file encoding. This is very useful for handling various text files and ensures that file content is read and returned correctly.

3. **Chunked Response Data Transfer**:
   The server implements chunked response capability, which allows it to limit download speeds and support resumable downloads. This is practical for sharing large files and can enhance user experience.

4. **Handling POST Requests**:
   The server has basic capabilities for handling POST requests. This means that users can submit data via forms, and the server can correctly receive and process these requests.
