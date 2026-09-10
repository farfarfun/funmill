# Windmill 简单部署

以下方式不使用 Docker，适用于 Linux x86_64。需要一套可用的 PostgreSQL，
以及 `python3`、`uv` 和 Bash；Windmill 会自动初始化数据库表。

## 1. 安装 Funmill 和 Windmill

```bash
uv sync
uv run funmill install windmill
```

Windmill 会安装到 `~/.farfarfun/funmill/services/windmill/`。安装器会校验
官方发行包的 SHA-256；设置 `FUNMILL_HOME` 可以修改安装目录。

## 2. 准备数据库

在 PostgreSQL 中创建用户和数据库：

```bash
sudo -u postgres createuser --pwprompt windmill
sudo -u postgres createdb --owner=windmill windmill
```

然后确认下面的连接可以使用：

```text
postgres://windmill:数据库密码@127.0.0.1:5432/windmill
```

## 3. 启动 Windmill

`standalone` 模式会在一个进程中同时运行 Server 和一个 Worker：

```bash
DATABASE_URL='postgres://windmill:数据库密码@127.0.0.1:5432/windmill' \
MODE=standalone \
PORT=8805 \
BASE_URL='http://127.0.0.1:8805' \
SERVER_BIND_ADDR=127.0.0.1 \
uv run funmill start windmill
```

打开 <http://127.0.0.1:8805>，首次登录使用：

```text
admin@windmill.dev / changeme
```

立即修改密码，并在 `admins` workspace 中创建 API Token。

## 4. 启动 Funmill API

回到 Funmill 仓库根目录执行：

```bash
FUNMILL_API_KEY='自行设置的接口密钥' \
FUNMILL_BACKEND=windmill \
WINDMILL_URL='http://127.0.0.1:8805' \
WINDMILL_WORKSPACE=admins \
WINDMILL_TOKEN='刚创建的Windmill-Token' \
uv run funmill start
```

验证：

```bash
curl http://127.0.0.1:8000/health
FUNMILL_API_KEY='自行设置的接口密钥' ./scripts/smoke.sh
```

## 增加 Worker

需要更高并发时，在同一台或其他机器额外启动 Worker。每个进程使用不同的
`WORKER_SUFFIX`：

```bash
DATABASE_URL='postgres://windmill:数据库密码@127.0.0.1:5432/windmill' \
MODE=worker WORKER_SUFFIX=worker2 uv run funmill start windmill

DATABASE_URL='postgres://windmill:数据库密码@127.0.0.1:5432/windmill' \
MODE=worker WORKER_SUFFIX=worker3 uv run funmill start windmill
```

正式长期运行时，将上述命令交给现有的 systemd 或进程管理器即可。不要在
一个普通 Worker 进程中设置 `NUM_WORKERS>1`；Windmill 会因为隔离安全限制将
它回退为 1，多个独立 Worker 进程更明确。
