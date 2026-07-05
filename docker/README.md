# SOSO IPTV 家庭网关 · Docker 版

容器化的 IPTV 网关管理页 + 节目单/抓包/台标工具。基于 Alpine + busybox httpd，端口 8080。

> 说明：Docker 版主打**管理页、节目单在线编辑、抓包/VSP 工具与台标匹配**。真正的旁路由网络配置（br-iptv 桥、tc 二层透传、udpxy 模式切换、默认路由防抢占）依赖 OpenWrt 的 uci/procd，容器内为兼容 shim，相关“网络/接口”按钮在容器里不保证生效——这些请用 OpenWrt 的 ipk 版本。

## 快速开始

```bash
# 1. 按需修改 docker-compose.yml 里的 LAN_IP / EPG_HOST
# 2. 构建并启动
docker compose up -d --build
# 3. 浏览器打开
#    http://<宿主机IP>:8080/iptv/
```

默认登录账号：`admin / admin`（登录后在右上角“账号”里修改）。

## 环境变量

| 变量 | 说明 |
|------|------|
| `LAN_IP` | 播放器访问用的本机/路由 LAN IP，会用于页面与节目单里的 `http://LAN_IP:8080/...`、`:12123/...` |
| `EPG_HOST` | 可选，你的 php-epg 主机 IP（如 `192.168.1.100`）。设置后“一键增加台标”等会从 `http://EPG_HOST:8080/data/icon/` 本地台标库匹配；不设则跳过台标匹配 |
| `TZ` | 时区，默认 `Asia/Shanghai` |

## 目录挂载

- `./data/www` → `/www`：节目单（`new.m3u` / `new-logo.m3u`）与页面，持久化、可直接编辑。
- `./data/tmp` → `/tmp/iptv`：抓包等运行时数据。
- `./data/stats` → `/etc/soso-iptv-stats`：播放统计持久化（重启不丢）。

## 播放器使用

`http://<宿主机IP>:8080/new-logo.m3u`（带台标，推荐）。
