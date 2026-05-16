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
