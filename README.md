# PhotoWall

一个 Flask 摄影作品展示与管理项目。

## 本地运行

```powershell
E:\PhotoWall\.venv\Scripts\python.exe app.py
```

本地访问：

```text
http://127.0.0.1:5000/gallery
```

局域网访问：

```text
http://电脑局域网IP:5000/gallery
```

## 生产部署

生产环境建议配置持久化数据目录：

```text
DATA_DIR=/var/data
SECRET_KEY=替换成一个长随机字符串
```

启动命令：

```bash
gunicorn wsgi:app
```

## GitHub Pages 静态发布

本地管理后台仍然用 Flask 运行，公开展示页可以导出为纯静态文件：

```powershell
E:\PhotoWall\.venv\Scripts\python.exe export_static.py
```

默认会生成到：

```text
E:\PhotoWall\docs
```

GitHub Pages 可以选择从 `main` 分支的 `/docs` 目录发布。当前默认路径适配仓库页面：

```text
https://Ocean-code-3.github.io/PhotoWall/
```

如果以后绑定自定义域名并希望页面从根路径访问，可以这样导出：

```powershell
$env:STATIC_BASE_PATH=""
E:\PhotoWall\.venv\Scripts\python.exe export_static.py
```
